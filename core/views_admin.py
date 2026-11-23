"""Staff admin panel: Overview, Users, Payments. Anyone who is not staff gets a 404, so the panel's existence isn't revealed."""
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import adminstats, services
from .models import Account, AICall, Budget, Debt, PaymentRequest, Transaction, User


def staff_only(view):
    @wraps(view)
    @login_required
    def wrapper(request, *a, **kw):
        if not request.user.is_staff:
            raise Http404
        return view(request, *a, **kw)

    return wrapper


def _ctx(tab, **extra):
    return {"nav": "admin", "tab": tab, "pending_payments": PaymentRequest.objects.filter(status="pending").count(), **extra}


@staff_only
def overview(request):
    s = adminstats.overview()
    top = max((n for _, n in s["usage"]), default=0)
    return render(request, "core/admin_overview.html", _ctx("overview", s=s, usage_max=top, ai_user_limit=settings.AI_DAILY_LIMIT))


@staff_only
def users(request):
    q, status, sort = request.GET.get("q", "").strip(), request.GET.get("status", ""), request.GET.get("sort", "new")
    page = Paginator(adminstats.user_rows(q, status, sort), 50).get_page(request.GET.get("page"))
    qs = request.GET.copy(); qs.pop("page", None)
    return render(request, "core/admin_users.html", _ctx("users", page=page, q=q, status=status, sort=sort, total=page.paginator.count, qs=qs.urlencode(),
                  status_options=[("active", "Active"), ("deactivated", "Deactivated"), ("staff", "Staff"), ("paying", "Paying"), ("trial", "On free trial")],
                  sort_options=[("new", "Newest"), ("old", "Oldest"), ("entries", "Most entries"), ("ai", "Most AI today"), ("login", "Last login")]))


@staff_only
def user_detail(request, pk):
    u = get_object_or_404(User, pk=pk)
    start = services.day_start()
    stats = [("Transactions", Transaction.objects.filter(user=u).count()), ("Debts", Debt.objects.filter(user=u).count()),
             ("Accounts", Account.objects.filter(user=u).count()), ("Budgets", Budget.objects.filter(user=u).count()),
             ("AI requests today", AICall.objects.filter(user=u, created_at__gte=start).count())]
    return render(request, "core/admin_user.html", _ctx("users", u=u, stats=stats, payments=PaymentRequest.objects.filter(user=u).defer("screenshot")[:20]))


@require_POST
@staff_only
def user_active(request, pk):
    u = get_object_or_404(User, pk=pk)
    if u.pk == request.user.pk or u.is_superuser:
        messages.error(request, "You can't deactivate yourself or a superuser.")
    else:
        u.is_active = not u.is_active
        u.save(update_fields=["is_active"])
        messages.success(request, f"{u.email} is now {'active' if u.is_active else 'deactivated (cannot log in)'}.")
    return redirect("admin_user", pk=pk)


@staff_only
def payments(request):
    status = request.GET.get("status", "pending")
    qs = PaymentRequest.objects.select_related("user").defer("screenshot")
    if status in ("pending", "approved", "rejected"):
        qs = qs.filter(status=status)
    counts = {r["status"]: r["n"] for r in PaymentRequest.objects.values("status").annotate(n=Count("id"))}
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "core/admin_payments.html", _ctx("payments", page=page, status=status, counts=counts,
                                                              tabs=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("all", "All")]))


@require_POST
@staff_only
def payment_review(request, pk):
    pay = get_object_or_404(PaymentRequest, pk=pk)
    action = request.POST.get("action")
    note = request.POST.get("note", "").strip()
    if action == "approve" and pay.review("approved", request.user, note):
        messages.success(request, f"Approved. {pay.user.email} now has the {pay.plan} plan until {timezone.localtime(pay.user.paid_until):%d %b %Y}.")
    elif action == "reject" and pay.review("rejected", request.user, note or "We could not verify this payment. Please contact us or submit it again."):
        messages.success(request, "Rejected. The user can see the reason.")
    else:
        messages.error(request, "That payment has already been reviewed.")
    return redirect(request.POST.get("next") if (request.POST.get("next") or "").startswith("/admin/") else "admin_payments")
