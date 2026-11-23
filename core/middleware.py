from django.utils import translation


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
