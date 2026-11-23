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
from django.core.validators import validate_email
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from . import ai, services
from .models import AIInsight, Budget, Category, ChatMessage, Transaction, User

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
                  {"nav": "entry", "cats": cats, "prefill": request.GET.get("text", "")[:2000], "today": timezone.localdate()})


@require_POST
@ai_endpoint
def api_extract(request):
    text = str(body_json(request).get("text", "")).strip()
    if not text:
        return JsonResponse({"error": "Write something first."}, status=400)
    rows = ai.extract_transactions(request.user, text, category_names(request.user))
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
    today = timezone.localdate()
    objs = []
    for i, t in enumerate(items, 1):
        kind = t.get("type")
        amount = money(t.get("amount"))
        cat = cats.get((kind, t.get("category")))
        try:
            d = datetime.strptime(str(t.get("date")), "%Y-%m-%d").date()
        except ValueError:
            d = None
        if kind not in ("expense", "income") or amount is None or cat is None or d is None or d > today + timedelta(days=1):
            return JsonResponse({"error": f"Row {i} has an invalid amount, category or date."}, status=400)
        conf = t.get("confidence")
        objs.append(Transaction(
            user=request.user, kind=kind, amount=amount, category=cat, date=d, note=str(t.get("note", ""))[:200],
            ai_generated=bool(t.get("ai")), ai_confidence=int(conf) if isinstance(conf, (int, float)) else None))
    Transaction.objects.bulk_create(objs)
    return JsonResponse({"saved": len(objs)})


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
    return qs


@login_required
def transactions(request):
    page = Paginator(_filtered(request), 30).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "core/transactions.html", {
        "nav": "transactions", "page": page, "cats": Category.objects.filter(user=request.user),
        "qs": params.urlencode(), "f": request.GET})


@require_POST
@login_required
def transaction_delete(request, pk):
    Transaction.objects.filter(user=request.user, pk=pk).delete()
    messages.success(request, "Transaction deleted.")
    return redirect(request.POST.get("next") if url_has_allowed_host_and_scheme(request.POST.get("next"), {request.get_host()}) else "transactions")


@login_required
def transactions_csv(request):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="expenseai-transactions.csv"'
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
    "Which category did I spend most on?",
    "Compare this month with last month",
    "Am I within my budgets?",
    "এই মাসে কত টাকা সঞ্চয় করতে পারবো?",
    "Give me 3 tips to save more",
]


@login_required
def assistant(request):
    today = timezone.localdate()
    first, last = services.month_bounds(today.year, today.month)
    t = services.totals(request.user, first, last)
    top = (services.category_spend(request.user, first, last) or [None])[0]
    budgets_ = services.budget_status(request.user, first, last)
    used = round(sum(b["spent"] for b in budgets_) / sum(b["limit"] for b in budgets_) * 100) if budgets_ else 0
    history = list(ChatMessage.objects.filter(user=request.user).order_by("-id")[:30])[::-1]
    return render(request, "core/assistant.html", {
        "nav": "assistant", "history": history, "suggested": SUGGESTED, "t": t, "top": top,
        "budget_used": used, "days_left": (last - today).days})


@require_POST
@ai_endpoint
def api_chat(request):
    q = str(body_json(request).get("message", "")).strip()[:1000]
    if not q:
        return JsonResponse({"error": "Type a question."}, status=400)
    history = list(ChatMessage.objects.filter(user=request.user).order_by("-id")[:6])[::-1]
    answer = ai.chat(request.user, q, services.ai_context(request.user, timezone.localdate()), history)
    ChatMessage.objects.bulk_create([ChatMessage(user=request.user, role="user", content=q),
                                     ChatMessage(user=request.user, role="assistant", content=answer)])
    return JsonResponse({"answer": answer})


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
