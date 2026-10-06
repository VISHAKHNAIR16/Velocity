"""Settings used only when running the automated tests."""

import tempfile

from .settings import *  # noqa: F403

# Throwaway in-memory database. Never touches Neon.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# Plain local storage: tests never upload to Cloudinary.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MEDIA_ROOT = tempfile.mkdtemp(prefix="velocity-test-media-")

# A fast (insecure) hasher keeps the test suite quick. Tests only.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Don't let production security settings interfere with the test client.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
