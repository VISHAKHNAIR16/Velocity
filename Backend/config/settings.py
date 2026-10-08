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
        "DIRS": [],
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
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # Secure by default: every endpoint requires login unless a view
    # explicitly opts out (e.g. register, login, health check).
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # JSON only, with no browsable HTML API. Matches the API-first rule.
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "EXCEPTION_HANDLER": "config.exceptions.api_exception_handler",
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
        "user": "600/hour",     # signed-in browsing and normal CRUD
        "login": "10/minute",   # brute-force protection
        "preview": "120/minute",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,  # a new refresh token is issued on each refresh
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
