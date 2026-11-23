"""Read-side analytics shared by pages and the AI context builders."""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncDay, TruncMonth

from .models import Budget, Transaction

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
        .values("category__id", "category__name", "category__color")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    return [
        {"id": r["category__id"], "name": r["category__name"] or "Uncategorized",
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
