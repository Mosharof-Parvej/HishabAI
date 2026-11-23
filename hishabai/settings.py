import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEBUG = os.environ.get("DEBUG") == "1"
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set the SECRET_KEY environment variable.")
    SECRET_KEY = "dev-insecure-key"

ALLOWED_HOSTS = [".vercel.app", "localhost", "127.0.0.1"] + [
    h for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h
]
CSRF_TRUSTED_ORIGINS = ["https://*.vercel.app"] + [
    o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.UserLanguageMiddleware",
    "core.middleware.VisitorMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "hishabai.urls"
WSGI_APPLICATION = "hishabai.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context.shell",
            ],
        },
    }
]

_db = dj_database_url.parse(
    os.environ.get("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=0
)
if _db["ENGINE"].endswith("postgresql"):
    _db["DISABLE_SERVER_SIDE_CURSORS"] = True  # Neon pooler (pgbouncer) safe
DATABASES = {"default": _db}

AUTH_USER_MODEL = "core.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"

LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
WHITENOISE_USE_FINDERS = True  # serve app static files directly; no collectstatic step needed
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

# --- App settings ---
TRIAL_DAYS = 15
PLANS = {
    "monthly": {"label": "Monthly", "price": 149, "days": 30},
    "yearly": {"label": "Yearly", "price": 1299, "days": 365},
}
SITE_AUTHOR = os.environ.get("SITE_AUTHOR", "")  # optional footer credit; the team is credited on the About page
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")  # shown on the About & contact page when set

# Manual payments: users send money to these accounts, upload proof, and staff approve it in the admin panel.
# Only methods with a value are shown. Set them as Vercel environment variables.
PAYMENT_ACCOUNTS = {
    "bkash": {"number": os.environ.get("PAY_BKASH", ""), "holder": os.environ.get("PAY_BKASH_NAME", ""), "kind": os.environ.get("PAY_BKASH_TYPE", "Personal")},
    "nagad": {"number": os.environ.get("PAY_NAGAD", ""), "holder": os.environ.get("PAY_NAGAD_NAME", ""), "kind": os.environ.get("PAY_NAGAD_TYPE", "Personal")},
    "rocket": {"number": os.environ.get("PAY_ROCKET", ""), "holder": os.environ.get("PAY_ROCKET_NAME", ""), "kind": os.environ.get("PAY_ROCKET_TYPE", "Personal")},
    "bank": {"bank": os.environ.get("PAY_BANK_NAME", ""), "holder": os.environ.get("PAY_BANK_ACCOUNT_NAME", ""), "number": os.environ.get("PAY_BANK_ACCOUNT_NO", ""),
             "branch": os.environ.get("PAY_BANK_BRANCH", ""), "routing": os.environ.get("PAY_BANK_ROUTING", "")},
}
PAYMENT_PENDING_LIMIT = 3  # unreviewed payment requests per user

AI_PRIMARY = os.environ.get("AI_PRIMARY", "gemini")
AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT", "60"))  # requests per user per day
AI_APP_DAILY_REQUESTS = int(os.environ.get("AI_APP_DAILY_REQUESTS", "400"))  # whole app, per day: protects the free-tier keys
AI_APP_DAILY_TOKENS = int(os.environ.get("AI_APP_DAILY_TOKENS", "1000000"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_STT_MODEL = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3")  # speech-to-text for the mic button
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
