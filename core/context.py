from django.conf import settings
from django.utils import timezone

from . import services


def shell(request):
    """Values every page needs: trial length and the repayment reminders shown in the bell."""
    ctx = {"TRIAL_DAYS": settings.TRIAL_DAYS, "SITE_AUTHOR": settings.SITE_AUTHOR}
    if getattr(request, "user", None) and request.user.is_authenticated:
        ctx["reminders"] = services.reminders(request.user, timezone.localdate())
        ctx["ai_usage"] = services.ai_usage(request.user)
    return ctx
