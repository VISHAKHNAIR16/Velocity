"""
Django settings for the Velocity GST Billing platform.

All environment-specific values come from environment variables (.env locally,
the Render dashboard in production). Nothing secret is hardcoded here.
"""

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url  # pyright: ignore[reportMissingImports]
from django.core.exceptions import (
    ImproperlyConfigured,  # pyright: ignore[reportMissingModuleSource]
)
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env for local development. In production the variables already exist
# in the environment, and load_dotenv() simply does nothing.
load_dotenv(BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# Helpers for reading environment variables
# ---------------------------------------------------------------------------
def env_bool(name: str, default: bool = False) -> bool:
    """Read a True/False env variable ("1", "true", "yes", "on" count as True)."""
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    """Read a comma-separated env variable into a clean list of strings."""
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core security settings
# ---------------------------------------------------------------------------
# Defaults to False so a forgotten variable can never expose debug pages.
DEBUG = env_bool("DEBUG", False)

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "insecure-local-development-key-only"
    else:
        # Fail loudly: running production without a real key is unsafe.
        raise ImproperlyConfigured("The SECRET_KEY environment variable is required.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "corsheaders",
    # Local apps are added here as we build them (accounts, parties, ...)
    "apps.accounts",
    "apps.core",
    "apps.parties",
    "apps.inventory",
    # Phase 1.4: added now (before its models exist) so the Step B calculator
    # and its tests are importable and discoverable.
    "apps.invoices",
    "apps.documents",
    # Phase 3: payments (3.1) — PaymentRecord model and /api/v1/payments/.
    "apps.payments",
    # Phase 3: dashboard (3.3) — /api/v1/dashboard/stats/ and /api/v1/dashboard/low-stock/.
    "apps.dashboard",
    # Provides OutstandingToken/BlacklistedToken so a refresh token can be
    # invalidated. Without it a stolen refresh token is valid for its full
    # lifetime with no way to revoke it, and "sign out of this device" cannot
    # exist. Requires `rest_framework_simplejwt` in INSTALLED_APPS.
    "rest_framework_simplejwt.token_blacklist",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise must sit directly after SecurityMiddleware.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # CORS must come before anything that can generate a response (CommonMiddleware).
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Templates are only needed for the Django admin site. The API itself
# returns JSON only.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Invoice PDF templates live in Backend/Invoice_Templates/, named
        # "Invoice_Templates/invoice_classic.html" etc. DIRS points at BASE_DIR
        # rather than at the folder itself so those names resolve; APP_DIRS
        # alone only looks inside an app's own templates/ directory.
        "DIRS": [BASE_DIR],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# DATABASE_URL set   -> PostgreSQL (Supabase in production)
# DATABASE_URL empty -> local SQLite file, so you can start with zero setup
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,  # reuse connections for 60s instead of reconnecting each request
        conn_health_checks=True,  # drop dead connections instead of erroring
    )
}

# Neon's pooled endpoint uses TRANSACTION pooling, which breaks server-side
# cursors: Postgres will not keep a cursor alive across transaction boundaries.
# Django's `.iterator()` uses them, so through the pooler the 4.2 exports fail
# with an opaque "cursor does not exist".
#
# Force client-side cursors on PostgreSQL. (`DISABLE_SERVER_SIDE_CURSORS` is the
# **psycopg2** spelling and does not exist in psycopg3, which this project uses -
# passing it to `dj_database_url.config()` is a TypeError at import time. With
# psycopg3 the supported route is Django's `OPTIONS["cursor_factory"]`.)
if os.getenv("DATABASE_URL", "").startswith("postgres"):
    from psycopg import ClientCursor

    _options = dict(DATABASES["default"].get("OPTIONS") or {})
    _options["cursor_factory"] = ClientCursor
    DATABASES["default"]["OPTIONS"] = _options

# ---------------------------------------------------------------------------
# Cache (decision 24)
# ---------------------------------------------------------------------------
# The throttle counters live in the cache, so this decides where rate-limit
# state is kept. Django's default `LocMemCache` is **per process**: it resets on
# every deploy, and with more than one worker each worker counts separately, so
# a user gets N times the intended rate and a deploy wipes every counter.
#
# `DatabaseCache` needs one-time setup:
#     python manage.py createcachetable
# The cache table is created by a migration in `apps.core`, so a fresh database
# has it without any manual step.
CACHES = {
    # `ResilientDatabaseCache` is `DatabaseCache` with one difference: if the
    # `django_cache` table is missing or unreachable, it degrades to an
    # in-process cache instead of raising. That matters more than it looks,
    # because DRF throttles read the cache BEFORE the view body runs - so an
    # unusable cache used to turn every throttled endpoint into a bare 500,
    # which is precisely how a total login outage happened. See
    # config/cache.py and FUTURE_CHECKLIST.md.
    "default": {
        "BACKEND": "config.cache.ResilientDatabaseCache",
        "LOCATION": "django_cache",
        # Never let the cache itself become an outage: if the table is missing
        # the app must still boot, and throttling degrades rather than 500s.
        "TIMEOUT": 300,
    }
}


# ---------------------------------------------------------------------------
# Passwords, language, time
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"  # Invoice dates must follow Indian time
USE_I18N = True
USE_TZ = True  # Store UTC in the DB, convert on display

AUTH_USER_MODEL = "accounts.User"  # must be set BEFORE the first migrate

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Static & media files
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"  # where `collectstatic` gathers files

STORAGES = {
    # Uploaded files (logos, PDFs). Local disk by default; Cloudinary when configured below.
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # WhiteNoise compresses and fingerprints static files for long-term caching.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Cloudinary is switched on only when CLOUDINARY_URL exists, so local
# development never needs a Cloudinary account.
if os.getenv("CLOUDINARY_URL"):
    INSTALLED_APPS += ["cloudinary_storage", "cloudinary"]
    STORAGES["default"] = {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"}


# ---------------------------------------------------------------------------
# Django REST Framework + JWT
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    # How many proxy hops to trust when reading X-Forwarded-For for the client's
    # IP (the throttle key and the "signed in from" label both use it).
    #
    # Render sits in front of us and forwards the real client IP in
    # X-Forwarded-For, so without this DRF sees the *proxy's* address and every
    # anonymous caller shares one bucket: one attacker exhausting `anon` locks
    # out every login on the platform. One hop is correct for a single CDN/proxy
    # in front of the app.
    #
    # **Verify this on the deployed service** (1.4.9 finding #14): a wrong value
    # fails silently, and the wrong direction lets a client spoof the header to
    # escape throttling entirely. Log X-Forwarded-For once from a staging
    # request and confirm it holds exactly one address.
    "NUM_PROXIES": 1,
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # Secure by default: every endpoint requires login unless a view
    # explicitly opts out (e.g. register, login, health check).
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # JSON only, with no browsable HTML API. Matches the API-first rule.
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "EXCEPTION_HANDLER": "config.exceptions.throttle_exception_handler",
    "PAGE_SIZE": 20,
    # Throttling. Applied globally; a view opts into a stricter named rate with
    # `throttle_scope`. POST /invoices/preview/ runs the whole GST calculation on
    # every keystroke-debounce, so it gets its own scope below.
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/hour",      # unauthenticated (register / login)
        # Raised from 600/hour in 2.0.5 (decision 24). 600/hour is 10 requests
        # per minute across EVERY endpoint, and item/party autocomplete alone
        # exhausts it - a cashier gets locked out mid-invoice, which is worse
        # than a slightly looser brute-force limit on an endpoint that already
        # has its own tighter scopes.
        "user": "5000/hour",
        # Applied to every unauthenticated auth endpoint: login, register and
        # token refresh. Added in 2.0.5 - `login` and `preview` already had
        # scopes, but register and refresh had none and fell through to `anon`
        # (1.4.9 finding #3).
        "auth": "10/minute",
        "login": "10/minute",   # kept for backwards compatibility with decision 18's tests
        "preview": "120/minute",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,  # a new refresh token is issued on each refresh
    # Without this, rotating a refresh token leaves the OLD one valid for its
    # full 7 days, so a token that should have been retired keeps working - and
    # "log out everywhere" could never actually invalidate anything.
    "BLACKLIST_AFTER_ROTATION": True,
}


# ---------------------------------------------------------------------------
# CORS: which frontend origins may call this API
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")


# ---------------------------------------------------------------------------
# Production security (applies automatically when DEBUG=False)
# ---------------------------------------------------------------------------
if not DEBUG:
    # Render terminates HTTPS at its proxy and forwards this header to Django.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    # Let the platform health check work even if it arrives over plain HTTP.
    SECURE_REDIRECT_EXEMPT = [r"^api/v1/health/$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 3600  # start small; raise to 1 year once everything is verified
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True


# --- Diagnostics -------------------------------------------------------------
# Set this in the host's environment to unlock the `?token=` half of
# /api/v1/health/diagnostics/, which reports the live exception from the login
# path. Without it that half stays off, and the endpoint returns only booleans.
# It is read with getattr() rather than os.getenv so an unset value cannot break
# start-up.
DIAGNOSTICS_TOKEN = os.getenv("DIAGNOSTICS_TOKEN", "")
