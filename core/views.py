import csv
import json
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Sum
from django.core.validators import validate_email
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from django.templatetags.static import static

from . import ai, catalog, services
from .models import AIInsight, Account, Budget, Category, Debt, DebtPayment, Transaction, User

# ---------------------------------------------------------------- helpers


def ai_endpoint(view):
    """JSON AI endpoint: needs login, an active trial/plan, and maps AI errors to JSON."""

    @wraps(view)
    def wrapper(request, *a, **kw):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Please log in."}, status=401)
        if not request.user.has_access:
            return JsonResponse({"error": "Your free trial has ended. Please subscribe to keep using AI."}, status=402)
        try:
            return view(request, *a, **kw)
        except ai.AIQuotaExceeded as e:
            return JsonResponse({"error": str(e)}, status=429)
        except ai.AIUnavailable as e:
            return JsonResponse({"error": f"AI is unavailable right now. {e}"}, status=503)

    return wrapper


def body_json(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


def parse_month(value, today):
    try:
        d = datetime.strptime(value, "%Y-%m").date()
        return d if d <= today else today.replace(day=1)
    except (TypeError, ValueError):
        return today.replace(day=1)


def pct_change(cur, prev):
    return round((cur - prev) / prev * 100) if prev else None


def category_names(user):
    cats = {"expense": [], "income": []}
    for c in Category.objects.filter(user=user):
        cats[c.kind].append(c.name)
    return cats


def account_names(user):
    return list(Account.objects.filter(user=user).values_list("name", flat=True))


def parse_date(value, default=None):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return default


def user_account(user, pk):
    return Account.objects.filter(user=user, pk=pk).first() if str(pk or "").isdigit() else None


REMIND_CHOICES = {0, 1, 3, 7}


def parse_remind(value, due):
    """'' / missing / no due date -> None (no reminder); otherwise one of REMIND_CHOICES."""
    if due is None or not str(value or "").isdigit() or int(value) not in REMIND_CHOICES:
        return None
    return int(value)


def money(value):
    try:
        v = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return v if 0 < v <= Decimal("999999999") else None


# ---------------------------------------------------------------- auth


def _safe_next(request):
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    ok = url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure())
    return nxt if ok else reverse("dashboard")


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    error = ""
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        user = authenticate(request, username=email, password=request.POST.get("password", ""))
        if user:
            login(request, user)
            if not request.POST.get("remember"):
                request.session.set_expiry(0)
            return redirect(_safe_next(request))
        error = "Wrong email or password."
    return render(request, "core/login.html", {"error": error, "google": bool(settings.GOOGLE_CLIENT_ID), "mode": "login"})


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    error = ""
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        name = request.POST.get("name", "").strip()[:80]
        try:
            validate_email(email)
            if User.objects.filter(email=email).exists():
                raise ValidationError("An account with this email already exists.")
            validate_password(password)
        except ValidationError as e:
            error = " ".join(e.messages)
        else:
            user = User.objects.create_user(email, password, name=name)
            login(request, user)
            return redirect("dashboard")
    return render(request, "core/login.html", {"error": error, "google": bool(settings.GOOGLE_CLIENT_ID), "mode": "signup"})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


def google_start(request):
    if not settings.GOOGLE_CLIENT_ID:
        messages.error(request, "Google sign-in is not configured.")
        return redirect("login")
    state = secrets.token_urlsafe(24)
    request.session["google_state"] = state
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": request.build_absolute_uri(reverse("google_callback")),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


def google_callback(request):
    fail = lambda m: (messages.error(request, m), redirect("login"))[1]  # noqa: E731
    state = request.session.pop("google_state", None)
    if not state or state != request.GET.get("state") or "code" not in request.GET:
        return fail("Google sign-in failed. Please try again.")
    try:
        tok = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": request.GET["code"],
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": request.build_absolute_uri(reverse("google_callback")),
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        tok.raise_for_status()
        info = requests.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {tok.json()['access_token']}"},
            timeout=15,
        ).json()
    except (requests.RequestException, KeyError, ValueError):
        return fail("Google sign-in failed. Please try again.")
    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified"):
        return fail("Your Google email is not verified.")
    user = User.objects.filter(email=email).first()
    if not user:
        user = User.objects.create_user(email, None, name=info.get("name", "")[:80])
        user.set_unusable_password()
        user.save(update_fields=["password"])
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("dashboard")


# ---------------------------------------------------------------- dashboard


@login_required
def dashboard(request):
    today = timezone.localdate()
    first, last = services.month_bounds(today.year, today.month)
    pf, pl = services.month_bounds(*services.shift_month(today, -1).timetuple()[:2])
    cur, prev = services.totals(request.user, first, last), services.totals(request.user, pf, pl)
    cats = services.category_spend(request.user, first, last)
    total_exp = sum((c["total"] for c in cats), Decimal(0))
    for c in cats:
        c["share"] = round(c["total"] / total_exp * 100) if total_exp else 0
    ctx = {
        "today": today,
        "cur": cur,
        "income_change": pct_change(cur["income"], prev["income"]),
        "expense_change": pct_change(cur["expense"], prev["expense"]),
        "cats": cats[:6],
        "recent": Transaction.objects.filter(user=request.user).select_related("category")[:5],
        "series": services.monthly_series(request.user, first),
        "donut": {"labels": [c["name"] for c in cats[:6]], "values": [float(c["total"]) for c in cats[:6]],
                  "colors": [c["color"] for c in cats[:6]]},
        "nav": "dashboard",
    }
    balances = services.account_balances(request.user)
    ctx["accounts"] = balances
    ctx["accounts_total"] = sum((b for _, b in balances), Decimal(0))
    rows = services.debt_rows(request.user, today)
    ctx["debt_totals"] = services.debt_totals(rows)
    return render(request, "core/dashboard.html", ctx)


@require_GET
@ai_endpoint
def api_insight(request):
    today = timezone.localdate()
    cached = AIInsight.objects.filter(user=request.user, kind="dashboard", day=today).first()
    if cached:
        return JsonResponse({"text": cached.text})
    if not Transaction.objects.filter(user=request.user).exists():
        return JsonResponse({"text": ""})
    text = ai.insight(request.user, services.ai_context(request.user, today))
    AIInsight.objects.update_or_create(user=request.user, kind="dashboard", day=today, defaults={"text": text})
    return JsonResponse({"text": text})


# ---------------------------------------------------------------- smart entry


@login_required
def smart_entry(request):
    cats = list(Category.objects.filter(user=request.user).values("name", "kind"))
    return render(request, "core/smart_entry.html",
                  {"nav": "entry", "cats": cats, "accts": account_names(request.user), "prefill": request.GET.get("text", "")[:2000], "today": timezone.localdate()})


@require_POST
@ai_endpoint
def api_extract(request):
    text = str(body_json(request).get("text", "")).strip()
    if not text:
        return JsonResponse({"error": "Write something first."}, status=400)
    rows = ai.extract_transactions(request.user, text, category_names(request.user), account_names(request.user))
    if not rows:
        return JsonResponse({"error": "I couldn't find any transaction in that text."}, status=422)
    return JsonResponse({"transactions": rows})


@require_POST
@login_required
def api_save_transactions(request):
    items = body_json(request).get("transactions")
    if not isinstance(items, list) or not 0 < len(items) <= 50:
        return JsonResponse({"error": "Nothing to save."}, status=400)
    cats = {(c.kind, c.name): c for c in Category.objects.filter(user=request.user)}
    accts = {a.name: a for a in Account.objects.filter(user=request.user)}
    today = timezone.localdate()
    objs, debts = [], []
    for i, t in enumerate(items, 1):
        kind = t.get("type")
        amount = money(t.get("amount"))
        d = parse_date(t.get("date"))
        if kind in ("borrowed", "lent"):  # a debt: needs a person, no category
            person = str(t.get("note", "")).strip()[:80]
            due = parse_date(t.get("due_date")) if t.get("due_date") else None
            if amount is None or d is None or d > today + timedelta(days=1) or not person or (due and due < d):
                return JsonResponse({"error": f"Row {i}: enter the person, a valid amount and a repay date after the loan date."}, status=400)
            debts.append(Debt(user=request.user, direction=kind, person=person, amount=amount, date=d, due_date=due,
                              remind_days=parse_remind(t.get("remind_days"), due), account=accts.get(t.get("account"))))
            continue
        cat = cats.get((kind, t.get("category")))
        if kind not in ("expense", "income") or amount is None or cat is None or d is None or d > today + timedelta(days=1):
            return JsonResponse({"error": f"Row {i} has an invalid amount, category or date."}, status=400)
        conf = t.get("confidence")
        objs.append(Transaction(
            user=request.user, kind=kind, amount=amount, category=cat, account=accts.get(t.get("account")), date=d,
            note=str(t.get("note", ""))[:200],
            ai_generated=bool(t.get("ai")), ai_confidence=int(conf) if isinstance(conf, (int, float)) else None))
    Transaction.objects.bulk_create(objs)
    Debt.objects.bulk_create(debts)
    return JsonResponse({"saved": len(objs) + len(debts), "debts": len(debts)})


# ---------------------------------------------------------------- transactions


def _filtered(request):
    qs = Transaction.objects.filter(user=request.user).select_related("category")
    q, kind, cat = request.GET.get("q", "").strip(), request.GET.get("kind", ""), request.GET.get("cat", "")
    if q:
        qs = qs.filter(note__icontains=q)
    if kind in ("expense", "income"):
        qs = qs.filter(kind=kind)
    if cat.isdigit():
        qs = qs.filter(category_id=int(cat))
    acc = request.GET.get("acc", "")
    if acc.isdigit():
        qs = qs.filter(account_id=int(acc))
    d_from, d_to = parse_date(request.GET.get("from")), parse_date(request.GET.get("to"))
    if d_from and d_to and d_from > d_to:
        d_from, d_to = d_to, d_from
    if d_from:
        qs = qs.filter(date__gte=d_from)
    if d_to:
        qs = qs.filter(date__lte=d_to)
    return qs


@login_required
def transactions(request):
    qs = _filtered(request)
    agg = {r["kind"]: r["s"] for r in qs.order_by().values("kind").annotate(s=Sum("amount"))}
    income, expense = agg.get("income", Decimal(0)), agg.get("expense", Decimal(0))
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "core/transactions.html", {
        "nav": "transactions", "page": page, "cats": Category.objects.filter(user=request.user),
        "accts": Account.objects.filter(user=request.user),
        "qs": params.urlencode(), "f": request.GET,
        "sums": {"income": income, "expense": expense, "balance": income - expense, "count": page.paginator.count}})


@login_required
def transaction_add(request):
    cats = list(Category.objects.filter(user=request.user).values("id", "name", "kind"))
    accts = Account.objects.filter(user=request.user)
    today = timezone.localdate()
    ctx = {"nav": "transactions", "cats": cats, "accts": accts, "today": today, "v": {}}
    if request.method == "POST":
        p = request.POST
        kind = p.get("kind")
        amount = money(p.get("amount"))
        cat = Category.objects.filter(user=request.user, pk=p.get("category") or 0, kind=kind).first() if p.get("category", "").isdigit() else None
        d = parse_date(p.get("date"))
        if kind not in ("expense", "income") or amount is None or cat is None or d is None or d > today:
            ctx.update(v=p, error="Please enter a valid type, amount, category and a date that is not in the future.")
            return render(request, "core/transaction_add.html", ctx)
        Transaction.objects.create(user=request.user, kind=kind, amount=amount, category=cat,
                                   account=user_account(request.user, p.get("account")), date=d, note=p.get("note", "").strip()[:200])
        messages.success(request, "Transaction added.")
        return redirect("transaction_add" if p.get("again") else "transactions")
    return render(request, "core/transaction_add.html", ctx)


@require_POST
@login_required
def transaction_delete(request, pk):
    Transaction.objects.filter(user=request.user, pk=pk).delete()
    messages.success(request, "Transaction deleted.")
    return redirect(request.POST.get("next") if url_has_allowed_host_and_scheme(request.POST.get("next"), {request.get_host()}) else "transactions")


@login_required
def transactions_csv(request):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="hishabai-transactions.csv"'
    resp.write("﻿")  # BOM so Excel shows Bangla correctly
    w = csv.writer(resp)
    w.writerow(["Date", "Type", "Category", "Amount (BDT)", "Note"])
    for t in _filtered(request):
        # prefix formulas so spreadsheet apps don't execute user-controlled notes
        note = ("'" + t.note) if t.note[:1] in "=+-@" else t.note
        w.writerow([t.date, t.kind, t.category.name if t.category else "", t.amount, note])
    return resp


# ---------------------------------------------------------------- budgets & reports


@login_required
def budgets(request):
    today = timezone.localdate()
    month = parse_month(request.GET.get("month"), today)
    yearly = request.GET.get("view") == "yearly"
    if yearly:
        first, last, months = date(month.year, 1, 1), date(month.year, 12, 31), 12
        last = min(last, today)
    else:
        first, last = services.month_bounds(month.year, month.month)
        months = 1
    pf, pl = services.month_bounds(*services.shift_month(month, -1).timetuple()[:2])
    prev = {c["name"]: c["total"] for c in services.category_spend(request.user, pf, pl)}
    cats = services.category_spend(request.user, first, last)
    total = sum((c["total"] for c in cats), Decimal(0))
    for c in cats:
        c["share"] = round(c["total"] / total * 100) if total else 0
        p = prev.get(c["name"])
        c["vs"] = pct_change(c["total"], p) if p and not yearly else None
    month_options = [services.shift_month(today.replace(day=1), -i) for i in range(12)]
    saved = {b.category_id: b.limit for b in Budget.objects.filter(user=request.user)}
    expense_cats = [{"c": c, "limit": saved.get(c.id, "")} for c in Category.objects.filter(user=request.user, kind="expense")]
    return render(request, "core/budgets.html", {
        "nav": "budgets", "month": month, "yearly": yearly, "months_list": month_options,
        "status": services.budget_status(request.user, first, last, months), "cats": cats,
        "daily": services.daily_spending(request.user, *services.month_bounds(month.year, month.month)),
        "expense_cats": expense_cats, "total": total})


@require_POST
@login_required
def budgets_save(request):
    for c in Category.objects.filter(user=request.user, kind="expense"):
        raw = request.POST.get(f"limit_{c.id}", "").strip()
        if not raw:
            Budget.objects.filter(user=request.user, category=c).delete()
            continue
        v = money(raw)
        if v is not None:
            Budget.objects.update_or_create(user=request.user, category=c, defaults={"limit": v})
    messages.success(request, "Budgets saved.")
    return redirect("budgets")


@require_GET
@ai_endpoint
def api_budget_suggestions(request):
    tips = ai.budget_suggestions(request.user, services.ai_context(request.user, timezone.localdate()))
    return JsonResponse({"tips": tips})


# ---------------------------------------------------------------- assistant


SUGGESTED = [
    "How much did I spend on food this month?",
    "Show my biggest expenses last month",
    "এই মাসে আমার মোট আয় কত?",
    "bKash diye ki ki kinechi?",
    "Who do I owe money to?",
    "আমার কাছে কার কত পাওনা?",
]


@login_required
def ask(request):
    return render(request, "core/ask.html", {"nav": "ask", "suggested": SUGGESTED})


def _filter_labels(f):
    out = []
    if f["date_from"] or f["date_to"]:
        out.append(f"{f['date_from'] or '…'} → {f['date_to'] or 'today'}")
    if f["kind"]:
        out.append(f["kind"].title())
    out += f["categories"] + f["accounts"]
    if f["search"]:
        out.append(f"“{f['search']}”")
    return out


@require_POST
@ai_endpoint
def api_ask(request):
    q = str(body_json(request).get("question", "")).strip()[:500]
    if not q:
        return JsonResponse({"error": "Type a question."}, status=400)
    user, today = request.user, timezone.localdate()
    f = ai.parse_query(user, q, category_names(user), account_names(user))
    if f["intent"] == "debts":
        rows = services.debt_rows(user, today)
        if f["direction"]:
            rows = [r for r in rows if r["debt"].direction == f["direction"]]
        if f["person"]:
            rows = [r for r in rows if f["person"].lower() in r["debt"].person.lower()]
        rows = [r for r in rows if r["status"] != "settled"] or rows
        return JsonResponse({"intent": "debts", "title": f["title"], "totals": {k: float(v) for k, v in services.debt_totals(rows).items()},
                             "debts": [{"person": r["debt"].person, "direction": r["debt"].direction, "amount": float(r["debt"].amount),
                                        "remaining": float(r["remaining"]), "due": r["debt"].due_date.isoformat() if r["debt"].due_date else None,
                                        "status": r["status"]} for r in rows[:100]]})
    qs, summary = services.run_transaction_filter(user, f)
    return JsonResponse({
        "intent": "transactions", "title": f["title"], "filters": _filter_labels(f),
        "summary": {k: float(v) for k, v in summary.items()}, "shown": min(summary["count"], f["limit"]),
        "rows": [{"date": t.date.isoformat(), "kind": t.kind, "amount": float(t.amount), "note": t.note,
                  "category": t.category.name if t.category else "", "color": t.category.color if t.category else "#94A3B8",
                  "account": t.account.name if t.account else ""} for t in qs]})


# ---------------------------------------------------------------- accounts


@login_required
def accounts(request):
    balances = services.account_balances(request.user)
    cards = [{"a": a, "bal": bal, "opts": catalog.for_kind(a.kind) if a.kind != "cash" else []} for a, bal in balances]
    providers = [{"key": p["key"], "name": p["name"], "kind": p["kind"],
                  "logo": static(catalog.logo_file(p["key"])) if catalog.logo_file(p["key"]) else None}
                 for p in catalog.PROVIDERS.values() if p["kind"] != "cash"]
    return render(request, "core/accounts.html", {
        "nav": "accounts", "cards": cards, "total": sum((b for _, b in balances), Decimal(0)),
        "providers": providers, "cash_logo": static(catalog.logo_file("cash")), "error": request.GET.get("error", "")[:200]})


@require_POST
@login_required
def accounts_add(request):
    p = request.POST
    kind, key = p.get("kind"), p.get("provider", "")
    fail = lambda m: redirect(reverse("accounts") + "?" + urlencode({"error": m}))  # noqa: E731
    if kind not in dict(Account.KINDS):
        return fail("Choose an account type.")
    if kind == "cash":
        key = "cash"
    elif catalog.PROVIDERS.get(key, {}).get("kind") != kind:
        return fail("Choose a bank, wallet or card from the list.")
    name = (p.get("name") or catalog.PROVIDERS[key]["name"]).strip()[:60]
    try:
        opening = Decimal(p.get("opening_balance") or "0").quantize(Decimal("0.01"))
        if abs(opening) > Decimal("99999999999"):
            raise InvalidOperation
    except InvalidOperation:
        return fail("Opening balance must be a number.")
    if Account.objects.filter(user=request.user, name__iexact=name).exists():
        return fail(f"You already have an account named “{name}”.")
    Account.objects.create(user=request.user, kind=kind, provider=key, name=name, opening_balance=opening)
    messages.success(request, f"{name} added.")
    return redirect("accounts")


@require_POST
@login_required
def accounts_edit(request, pk):
    acc = Account.objects.filter(user=request.user, pk=pk).first()
    if not acc:
        return redirect("accounts")
    p = request.POST
    fail = lambda m: redirect(reverse("accounts") + "?" + urlencode({"error": m}))  # noqa: E731
    name = (p.get("name") or acc.name).strip()[:60]
    if Account.objects.filter(user=request.user, name__iexact=name).exclude(pk=acc.pk).exists():
        return fail(f"You already have an account named “{name}”.")
    key = p.get("provider", acc.provider)
    if acc.kind == "cash":
        key = "cash"
    elif key and catalog.PROVIDERS.get(key, {}).get("kind") != acc.kind:
        return fail("That bank, wallet or card does not match this account type.")
    try:  # the user types the CURRENT balance; store the opening balance that produces it
        wanted = Decimal(p.get("balance") or "0").quantize(Decimal("0.01"))
        if abs(wanted) > Decimal("99999999999"):
            raise InvalidOperation
    except InvalidOperation:
        return fail("Balance must be a number.")
    current = dict(services.account_balances(request.user))[acc]
    acc.opening_balance += wanted - current
    acc.name, acc.provider = name, key
    acc.save(update_fields=["name", "provider", "opening_balance"])
    messages.success(request, f"{name} updated.")
    return redirect("accounts")


@require_POST
@login_required
def accounts_delete(request, pk):
    acc = Account.objects.filter(user=request.user, pk=pk).first()
    if acc:
        acc.delete()  # its transactions stay, just unassigned
        messages.success(request, f"{acc.name} removed. Its transactions were kept.")
    return redirect("accounts")


# ---------------------------------------------------------------- debts


@login_required
def debts(request):
    today = timezone.localdate()
    rows = services.debt_rows(request.user, today)
    return render(request, "core/debts.html", {
        "nav": "debts", "today": today, "totals": services.debt_totals(rows),
        "owe": [r for r in rows if r["debt"].direction == "borrowed"],
        "owed": [r for r in rows if r["debt"].direction == "lent"],
        "accts": Account.objects.filter(user=request.user), "error": request.GET.get("error", "")[:200]})


@require_POST
@login_required
def debt_add(request):
    p, today = request.POST, timezone.localdate()
    fail = lambda m: redirect(reverse("debts") + "?" + urlencode({"error": m}))  # noqa: E731
    amount, person = money(p.get("amount")), p.get("person", "").strip()[:80]
    d = parse_date(p.get("date"), today)
    due = parse_date(p.get("due_date")) if p.get("due_date") else None
    if p.get("direction") not in ("borrowed", "lent") or not person or amount is None:
        return fail("Enter who, how much and whether you borrowed or lent.")
    if due and due < d:
        return fail("The due date cannot be before the date of the loan.")
    Debt.objects.create(user=request.user, direction=p["direction"], person=person, amount=amount, date=d, due_date=due,
                        remind_days=parse_remind(p.get("remind_days"), due),
                        account=user_account(request.user, p.get("account")), note=p.get("note", "").strip()[:200])
    messages.success(request, "Saved.")
    return redirect("debts")


@require_POST
@login_required
def debt_pay(request, pk):
    debt = Debt.objects.filter(user=request.user, pk=pk).first()
    fail = lambda m: redirect(reverse("debts") + "?" + urlencode({"error": m}))  # noqa: E731
    if not debt:
        return redirect("debts")
    row = next(r for r in services.debt_rows(request.user, timezone.localdate()) if r["debt"].pk == debt.pk)
    amount = money(request.POST.get("amount"))
    if amount is None or amount > row["remaining"]:
        return fail(f"Enter an amount between ৳0.01 and the remaining ৳{row['remaining']:.2f}.")
    DebtPayment.objects.create(debt=debt, amount=amount, date=parse_date(request.POST.get("date"), timezone.localdate()),
                               account=user_account(request.user, request.POST.get("account")) or debt.account)
    messages.success(request, "Payment recorded.")
    return redirect("debts")


@require_POST
@login_required
def debt_remind(request, pk):
    debt = Debt.objects.filter(user=request.user, pk=pk).first()
    if debt and debt.due_date:
        debt.remind_days = parse_remind(request.POST.get("remind_days"), debt.due_date)
        debt.save(update_fields=["remind_days"])
        messages.success(request, "Reminder updated.")
    return redirect("debts")


@require_POST
@login_required
def debt_delete(request, pk):
    Debt.objects.filter(user=request.user, pk=pk).delete()
    messages.success(request, "Deleted.")
    return redirect("debts")


# ---------------------------------------------------------------- subscription & settings


@login_required
def subscription(request):
    return render(request, "core/subscription.html", {"nav": "subscription", "plans": settings.PLANS,
                                                       "demo": settings.PAYMENTS_DEMO_MODE})


@require_POST
@login_required
def subscription_choose(request):
    plan = request.POST.get("plan")
    if plan not in settings.PLANS:
        return redirect("subscription")
    if not settings.PAYMENTS_DEMO_MODE:
        messages.error(request, "Online payment is not available yet.")
        return redirect("subscription")
    u = request.user
    base = u.paid_until if u.is_paid else timezone.now()
    u.plan, u.paid_until = plan, base + timedelta(days=settings.PLANS[plan]["days"])
    u.save(update_fields=["plan", "paid_until"])
    messages.success(request, f"{settings.PLANS[plan]['label']} plan activated (demo mode - no payment taken).")
    return redirect("subscription")


@login_required
def settings_view(request):
    if request.method == "POST":
        if request.POST.get("action") == "delete":
            if request.POST.get("confirm") == request.user.email:
                request.user.delete()
                logout(request)
                return redirect("login")
            messages.error(request, "Type your email exactly to confirm deletion.")
        else:
            request.user.name = request.POST.get("name", "").strip()[:80]
            request.user.language = "bn" if request.POST.get("language") == "bn" else "en"
            request.user.save(update_fields=["name", "language"])
            messages.success(request, "Settings saved.")
        return redirect("settings")
    return render(request, "core/settings.html", {"nav": "settings"})
