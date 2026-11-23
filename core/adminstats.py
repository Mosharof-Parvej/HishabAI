"""Numbers for the staff admin panel. Counts and totals only: amounts and anything users typed are never read here."""
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from . import services
from .models import Account, AICall, Budget, Debt, PaymentRequest, Transaction, User, VisitorDay


def _days(n=30):
    today = timezone.localdate()
    return [today - timedelta(days=i) for i in range(n - 1, -1, -1)]


def _series(qs, field, days, **agg):
    """{day: value} -> list aligned with `days`. qs is grouped by calendar day of `field`."""
    rows = qs.annotate(d=TruncDate(field)).values("d").annotate(v=agg.get("expr", Count("id")))
    by = {r["d"]: r["v"] or 0 for r in rows}
    return [by.get(d, 0) for d in days]


def overview():
    today, days = timezone.localdate(), _days(30)
    first30 = days[0]
    start_today = services.day_start()

    # ---- visitors (staff and bots are never recorded) ----
    vt = VisitorDay.objects.filter(day=today)
    visitors, logged = vt.count(), vt.filter(user__isnull=False).count()
    agg, agg_l = vt.aggregate(v=Sum("views"), s=Sum("seconds")), vt.filter(user__isnull=False).aggregate(s=Sum("seconds"))
    secs, secs_l = agg["s"] or 0, agg_l["s"] or 0
    seen = lambda n: VisitorDay.objects.filter(day__gte=today - timedelta(days=n - 1), user__isnull=False).values("user").distinct().count()  # noqa: E731

    # ---- users ----
    joined = User.objects.filter(date_joined__gte=start_today - timedelta(days=29))
    new_dates = [d for d in joined.annotate(d=TruncDate("date_joined")).values_list("d", flat=True)]
    week_ago = start_today - timedelta(days=6)
    active_ids = set()
    for model in (Transaction, Debt, AICall):
        active_ids |= set(model.objects.filter(created_at__gte=week_ago).values_list("user_id", flat=True))
    users_total = User.objects.count()

    # ---- AI, whole app ----
    ai_today = AICall.objects.filter(created_at__gte=start_today)
    ai_agg = ai_today.aggregate(n=Count("id"), t=Sum("tokens"))
    top = (ai_today.values("user__email").annotate(n=Count("id"), t=Sum("tokens")).order_by("-n", "-t")[:5])

    # ---- 30-day series ----
    vd = VisitorDay.objects.filter(day__gte=first30)
    vis_by = {}
    for r in vd.values("day").annotate(total=Count("id"), li=Count("id", filter=Q(user__isnull=False)), s=Sum("seconds")):
        vis_by[r["day"]] = r
    entries = lambda qs, **kw: _series(qs.filter(created_at__gte=start_today - timedelta(days=29), **kw), "created_at", days)  # noqa: E731
    ai30 = AICall.objects.filter(created_at__gte=start_today - timedelta(days=29))
    by_hand = entries(Transaction.objects.all(), ai_generated=False)
    with_ai = entries(Transaction.objects.all(), ai_generated=True)
    debts30 = entries(Debt.objects.all())

    kinds = {r["kind"]: r["n"] for r in ai30.values("kind").annotate(n=Count("id"))}
    pending = PaymentRequest.objects.filter(status="pending").count()
    return {
        "visitors": visitors, "logged": logged, "guests": visitors - logged, "views": agg["v"] or 0,
        "seen": {"today": logged, "7": seen(7), "30": seen(30)},
        "avg_secs": round(secs / visitors) if visitors else 0, "avg_secs_logged": round(secs_l / logged) if logged else 0, "total_secs": secs,
        "users_total": users_total, "google": User.objects.filter(password__startswith="!").count(),
        "new_today": sum(1 for d in new_dates if d == today), "new_7": sum(1 for d in new_dates if d >= today - timedelta(days=6)), "new_30": len(new_dates),
        "adding": len(active_ids), "deactivated": User.objects.filter(is_active=False).count(),
        "ai": {"n": ai_agg["n"], "t": ai_agg["t"] or 0, "n_cap": settings.AI_APP_DAILY_REQUESTS, "t_cap": settings.AI_APP_DAILY_TOKENS,
               "n_pct": min(100, round(ai_agg["n"] / settings.AI_APP_DAILY_REQUESTS * 100)) if settings.AI_APP_DAILY_REQUESTS else 0,
               "t_pct": min(100, round((ai_agg["t"] or 0) / settings.AI_APP_DAILY_TOKENS * 100)) if settings.AI_APP_DAILY_TOKENS else 0},
        "top_ai": [{"email": r["user__email"], "n": r["n"], "t": r["t"] or 0} for r in top],
        "pending_payments": pending,
        "charts": {
            "labels": [d.strftime("%d %b") for d in days],
            "logged": [vis_by.get(d, {}).get("li", 0) for d in days],
            "guests": [vis_by.get(d, {}).get("total", 0) - vis_by.get(d, {}).get("li", 0) for d in days],
            "avg_min": [round(vis_by[d]["s"] / vis_by[d]["total"] / 60, 2) if d in vis_by and vis_by[d]["total"] else 0 for d in days],
            "signups": [sum(1 for x in new_dates if x == d) for d in days],
            "ai": _series(ai30, "created_at", days),
            "with_ai": with_ai, "by_hand": by_hand, "debts": debts30,
        },
        "usage": [("Entries added with AI", sum(with_ai)), ("Entries added by hand", sum(by_hand)), ("Debts added", sum(debts30)),
                  ("AI requests (add, voice, Ask HishabAI)", sum(kinds.values()))],
        "all_time": [("Transactions", Transaction.objects.count()), ("Debts", Debt.objects.count()),
                     ("Money accounts", Account.objects.count()),
                     ("Budgets set", Budget.objects.count()),
                     ("Paid users", User.objects.filter(paid_until__gt=timezone.now()).count()),
                     ("Payments approved", PaymentRequest.objects.filter(status="approved").count())],
    }


def user_rows(q="", status="", sort="new"):
    start = services.day_start()
    qs = User.objects.annotate(entries=Count("transactions", distinct=True), ai_today=Count("aicall", filter=Q(aicall__created_at__gte=start), distinct=True))
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(name__icontains=q))
    now = timezone.now()
    qs = {"active": qs.filter(is_active=True), "deactivated": qs.filter(is_active=False), "staff": qs.filter(is_staff=True),
          "paying": qs.filter(paid_until__gt=now), "trial": qs.filter(trial_ends_at__gt=now).exclude(paid_until__gt=now)}.get(status, qs)
    return qs.order_by({"old": "date_joined", "entries": "-entries", "ai": "-ai_today", "login": "-last_login"}.get(sort, "-date_joined"), "-id")
