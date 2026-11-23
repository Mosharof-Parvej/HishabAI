from django.conf import settings
from django.utils import timezone

from . import i18n, services
from .templatetags.ui import taka


def shell(request):
    """Values every page needs: trial length and the repayment reminders shown in the bell."""
    ctx = {"TRIAL_DAYS": settings.TRIAL_DAYS, "SITE_AUTHOR": settings.SITE_AUTHOR, "CONTACT_EMAIL": settings.CONTACT_EMAIL}
    if getattr(request, "user", None) and request.user.is_authenticated:
        rems = ctx["reminders"] = services.reminders(request.user, timezone.localdate())
        ctx["reminders_data"] = [{
            "id": r["debt"].pk,
            "title": f"{r['debt'].person} · {i18n.tr('You owe' if r['debt'].direction == 'borrowed' else 'Owes you')} {taka(r['remaining'])}",
            "body": (i18n.tr("Reminder") if r["days"] is None else f"{abs(r['days'])}d {i18n.tr('overdue')}" if r["days"] < 0 else i18n.tr("Due today") if r["days"] == 0 else f"{i18n.tr('in')} {r['days']}d"),
        } for r in rems]
        ctx["ui_text"] = {k: i18n.tr(k) for k in ("Turn on notifications", "Notifications on", "Notifications blocked in browser settings")}
        ctx["ai_usage"] = services.ai_usage(request.user)
        if request.user.is_staff:
            from .models import PaymentRequest

            ctx["pending_payments"] = PaymentRequest.objects.filter(status="pending").count()
    return ctx
