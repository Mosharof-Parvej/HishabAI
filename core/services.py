"""Read-side analytics shared by pages and the AI context builders."""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.db.models.functions import TruncDay, TruncMonth

from .models import AICall, Account, Budget, Debt, DebtPayment, Transaction

ZERO = Decimal("0")


def month_bounds(year, month):
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    return first, last


def shift_month(d, delta):
    idx = d.year * 12 + d.month - 1 + delta
    return date(idx // 12, idx % 12 + 1, 1)


def totals(user, first, last):
    qs = Transaction.objects.filter(user=user, date__range=(first, last))
    agg = {r["kind"]: r["s"] for r in qs.values("kind").annotate(s=Sum("amount"))}
    income, expense = agg.get("income", ZERO), agg.get("expense", ZERO)
    savings_rate = round((income - expense) / income * 100) if income else 0
    return {"income": income, "expense": expense, "balance": income - expense, "savings_rate": savings_rate}


def category_spend(user, first, last):
    rows = (
        Transaction.objects.filter(user=user, kind="expense", date__range=(first, last))
        .values("category__id", "category__name", "category__color", "category__emoji")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    return [
        {"id": r["category__id"], "name": r["category__name"] or "Uncategorized", "emoji": r["category__emoji"] or "🏷️",
         "color": r["category__color"] or "#94A3B8", "total": r["total"]}
        for r in rows
    ]


def monthly_series(user, end_month, n=6):
    """Income/expense for the n months ending at end_month (a date on the 1st)."""
    start = shift_month(end_month, -(n - 1))
    rows = (
        Transaction.objects.filter(user=user, date__gte=start, date__lte=month_bounds(end_month.year, end_month.month)[1])
        .annotate(m=TruncMonth("date"))
        .values("m", "kind")
        .annotate(s=Sum("amount"))
    )
    data = defaultdict(lambda: {"income": ZERO, "expense": ZERO})
    for r in rows:
        data[r["m"]][r["kind"]] = r["s"]
    out = []
    for i in range(n):
        m = shift_month(start, i)
        out.append({"label": m.strftime("%b"), "income": float(data[m]["income"]), "expense": float(data[m]["expense"])})
    return out


def all_time_balance(user):
    agg = {r["kind"]: r["s"] for r in Transaction.objects.filter(user=user).values("kind").annotate(s=Sum("amount"))}
    return agg.get("income", ZERO) - agg.get("expense", ZERO)


def daily_series(user, first, last):
    """Per-day expense and income lists for first..last (inclusive)."""
    rows = (Transaction.objects.filter(user=user, date__range=(first, last)).annotate(d=TruncDay("date"))
            .values("d", "kind").annotate(s=Sum("amount")))
    by = {(r["d"], r["kind"]): float(r["s"]) for r in rows}
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    return {"labels": [f"{d.day:02d}" for d in days], "expense": [by.get((d, "expense"), 0.0) for d in days],
            "income": [by.get((d, "income"), 0.0) for d in days]}


def daily_spending(user, first, last):
    rows = (
        Transaction.objects.filter(user=user, kind="expense", date__range=(first, last))
        .annotate(d=TruncDay("date"))
        .values("d")
        .annotate(s=Sum("amount"))
    )
    by_day = {r["d"]: float(r["s"]) for r in rows}
    return [by_day.get(first + timedelta(days=i), 0.0) for i in range((last - first).days + 1)]


def budget_status(user, first, last, months=1):
    spent = {c["id"]: c["total"] for c in category_spend(user, first, last)}
    out = []
    for b in Budget.objects.filter(user=user).select_related("category").order_by("category__id"):
        limit = b.limit * months
        used = spent.get(b.category_id, ZERO)
        pct = round(used / limit * 100) if limit else 0
        out.append({"category": b.category, "limit": limit, "spent": used, "left": limit - used, "pct": pct,
                    "level": "danger" if pct >= 95 else "warn" if pct >= 80 else "ok"})
    return out


def ai_context(user, today):
    """Compact, privacy-minimal snapshot sent to the AI (no email/name)."""
    first, last = month_bounds(today.year, today.month)
    prev_first, prev_last = month_bounds(*shift_month(today, -1).timetuple()[:2])
    cur, prev = totals(user, first, last), totals(user, prev_first, prev_last)
    cats_prev = {c["name"]: c["total"] for c in category_spend(user, prev_first, prev_last)}
    recent = Transaction.objects.filter(user=user, date__gte=today - timedelta(days=60)).select_related("category")[:60]
    return {
        "today": today.isoformat(),
        "days_left_in_month": (last - today).days,
        "this_month": {k: float(v) for k, v in cur.items()},
        "last_month": {k: float(v) for k, v in prev.items()},
        "this_month_by_category": [
            {"category": c["name"], "spent": float(c["total"]), "last_month": float(cats_prev.get(c["name"], 0))}
            for c in category_spend(user, first, last)
        ],
        "budgets": [
            {"category": b["category"].name, "limit": float(b["limit"]), "spent": float(b["spent"]), "used_pct": b["pct"]}
            for b in budget_status(user, first, last)
        ],
        "recent_transactions": [
            {"date": t.date.isoformat(), "type": t.kind, "amount": float(t.amount),
             "category": t.category.name if t.category else None, "note": t.note}
            for t in recent
        ],
    }


# ---------------------------------------------------------------- AI usage

def ai_usage(user):
    """AI calls in the last 24h vs the per-user daily cap (the same window the cap itself enforces)."""
    from django.conf import settings
    from django.utils import timezone

    used = AICall.objects.filter(user=user, created_at__gte=timezone.now() - timedelta(hours=24)).count()
    limit = settings.AI_DAILY_LIMIT
    return {"used": used, "limit": limit, "pct": min(100, round(used / limit * 100)) if limit else 0}


# ---------------------------------------------------------------- accounts

def account_balances(user):
    """[(Account, balance)]: opening + income - expense, plus debt money in/out through that account."""
    bal = defaultdict(lambda: ZERO)
    accounts = list(Account.objects.filter(user=user))
    for a in accounts:
        bal[a.id] = a.opening_balance
    for r in Transaction.objects.filter(user=user, account__isnull=False).values("account_id", "kind").annotate(s=Sum("amount")):
        bal[r["account_id"]] += r["s"] if r["kind"] == "income" else -r["s"]
    for r in Debt.objects.filter(user=user, account__isnull=False).values("account_id", "direction").annotate(s=Sum("amount")):
        bal[r["account_id"]] += r["s"] if r["direction"] == "borrowed" else -r["s"]  # borrowed = cash in, lent = cash out
    for r in DebtPayment.objects.filter(debt__user=user, account__isnull=False).values("account_id", "debt__direction").annotate(s=Sum("amount")):
        bal[r["account_id"]] += -r["s"] if r["debt__direction"] == "borrowed" else r["s"]  # repaying out, being repaid in
    return [(a, bal[a.id]) for a in accounts]


# ---------------------------------------------------------------- debts

def debt_rows(user, today, open_only=False):
    """Each debt with paid/remaining and a status: settled | overdue | due_soon | open."""
    rows = []
    for d in Debt.objects.filter(user=user).select_related("account").annotate(paid=Sum("payments__amount")):
        paid = d.paid or ZERO
        remaining = d.amount - paid
        days = (d.due_date - today).days if d.due_date else None
        window = d.remind_days if d.remind_days is not None else REMIND_DAYS
        status = ("settled" if remaining <= 0 else "overdue" if days is not None and days < 0
                  else "due_soon" if days is not None and days <= window else "open")
        if open_only and status == "settled":
            continue
        notify = d.remind_days is not None and days is not None and days <= d.remind_days and remaining > 0
        rows.append({"debt": d, "paid": paid, "remaining": remaining, "days": days, "status": status, "notify": notify})
    return rows


REMIND_DAYS = 3


def debt_totals(rows):
    owe = sum((r["remaining"] for r in rows if r["debt"].direction == "borrowed" and r["status"] != "settled"), ZERO)
    owed = sum((r["remaining"] for r in rows if r["debt"].direction == "lent" and r["status"] != "settled"), ZERO)
    return {"owe": owe, "owed": owed}


def reminders(user, today):
    """Unsettled debts whose own reminder window has started (or that are overdue), most urgent first."""
    items = [r for r in debt_rows(user, today) if r["notify"]]
    return sorted(items, key=lambda r: r["days"])


# ---------------------------------------------------------------- AskHishabAI query

def run_transaction_filter(user, f):
    """f is the already-validated filter dict from ai.parse_query. Returns (rows queryset, summary)."""
    qs = Transaction.objects.filter(user=user).select_related("category", "account")
    if f.get("date_from"):
        qs = qs.filter(date__gte=f["date_from"])
    if f.get("date_to"):
        qs = qs.filter(date__lte=f["date_to"])
    if f.get("kind"):
        qs = qs.filter(kind=f["kind"])
    if f.get("categories"):
        qs = qs.filter(category__name__in=f["categories"])
    if f.get("accounts"):
        qs = qs.filter(account__name__in=f["accounts"])
    if f.get("search"):
        qs = qs.filter(Q(note__icontains=f["search"]) | Q(category__name__icontains=f["search"]))
    agg = {r["kind"]: r["s"] for r in qs.order_by().values("kind").annotate(s=Sum("amount"))}
    income, expense = agg.get("income", ZERO), agg.get("expense", ZERO)
    count = qs.count()
    order = {"date": ("-date", "-id"), "amount": ("-amount", "-id"), "oldest": ("date", "id")}[f.get("sort", "date")]
    return qs.order_by(*order)[: f.get("limit", 50)], {"count": count, "income": income, "expense": expense, "balance": income - expense}
