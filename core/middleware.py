import secrets

from django.conf import settings
from django.db import DatabaseError
from django.db.models import F
from django.utils import timezone, translation

from .models import VisitorDay


class UserLanguageMiddleware:
    """Activate the signed-in user's language (bn/en) for the request; Django then localises dates etc. too."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        lang = user.language if user is not None and user.is_authenticated and user.language in ("bn", "en") else "en"
        with translation.override(lang):
            request.LANGUAGE_CODE = lang
            return self.get_response(request)


BOT_WORDS = ("bot", "crawl", "spider", "slurp", "preview", "headless", "monitor", "uptime")
NO_TRACK = ("/static/", "/api/", "/admin", "/manage", "/payments/", "/favicon")
MAX_DAILY_SECONDS = 4 * 3600


def visitor_key(request):
    """The anonymous visitor id kept in a first-party cookie (never an IP address). Returns (key, is_new)."""
    key = request.COOKIES.get("hv", "")
    return (key, False) if len(key) == 24 and key.isalnum() else (secrets.token_hex(12), True)


def is_counted(request):
    ua = request.META.get("HTTP_USER_AGENT", "").lower()
    staff = getattr(request, "user", None) is not None and request.user.is_authenticated and request.user.is_staff
    return not staff and bool(ua) and not any(w in ua for w in BOT_WORDS)


class VisitorMiddleware:
    """Counts one visit per visitor per day plus page views, for the admin panel. Staff, bots and non-page requests are skipped."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            if (request.method == "GET" and response.status_code == 200 and "text/html" in response.get("Content-Type", "")
                    and not request.path.startswith(NO_TRACK) and is_counted(request)):
                key, new = visitor_key(request)
                user = request.user if request.user.is_authenticated else None
                row, _ = VisitorDay.objects.get_or_create(day=timezone.localdate(), key=key, defaults={"user": user})
                VisitorDay.objects.filter(pk=row.pk).update(views=F("views") + 1, **({"user": user} if user and not row.user_id else {}))
                if new:
                    response.set_cookie("hv", key, max_age=365 * 86400, samesite="Lax", httponly=True, secure=not settings.DEBUG)
        except DatabaseError:
            pass  # analytics must never break a page
        return response
