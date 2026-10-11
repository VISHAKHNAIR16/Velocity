# VELOCITY backend image.
#
# Built for Render. The build context is the REPOSITORY ROOT, not Backend/,
# because this file needs to COPY only the backend and must be able to skip the
# frontend (Web_Frontend is served separately, by Cloudflare Pages).
#
#   docker build -f Dockerfile -t velocity-backend .
#
# Why Docker at all (phase 2.3 decision 25/26): WeasyPrint needs Pango >= 1.44
# and Cairo as SYSTEM libraries. They cannot be pip-installed, and Render's
# native Python runtime does not ship them. Railway's does - which is why the
# VCRM project runs WeasyPrint there with no Dockerfile at all, and why that
# experience does not transfer here.
#
# MIGRATIONS ARE NOT RUN HERE, ON PURPOSE. Running `migrate` on container start
# races whenever more than one replica boots. Apply migrations as a deliberate
# pre-deploy step:
#     uv run python manage.py migrate

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
# python:3.12-slim-bookworm, pinned to MAJOR.MINOR only.
#
#   * 3.12 matches requires-python = ">=3.12" in pyproject.toml.
#   * bookworm is Debian 12, the same family as Render's own runtime, so the
#     apt package names below cannot drift between build and production.
#   * Pinning a PATCH ("3.12.11-slim") would be a trap: when that tag is
#     withdrawn the build fails for everyone with no obvious cause.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

# ---------------------------------------------------------------------------
# System libraries
# ---------------------------------------------------------------------------
# These are the exact packages WeasyPrint >= 66 documents. The one that catches
# people out: on Debian 12, `libharfbuzz0b` and `libharfbuzz-subset0` are TWO
# SEPARATE packages. Install only one and WeasyPrint fails at import with a
# CFFI error that names neither, which is a miserable thing to debug blind.
#
# fonts-dejavu-core gives Pango something to render before we ship our own
# U+20B9 (rupee) font - Helvetica has no rupee glyph and silently prints a box.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
      libpango-1.0-0 \
      libpangoft2-1.0-0 \
      libpangocairo-1.0-0 \
      libharfbuzz0b \
      libharfbuzz-subset0 \
      libcairo2 \
      libgdk-pixbuf-2.0-0 \
      shared-mime-info \
      fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
# requirements.txt is a committed, pinned lockfile produced by
# `uv export`. Installing from it rather than running `uv sync` inside the
# image deliberately avoids a second package manager as a failure surface.
#
# COPY'd on its own layer first so the dependency install is cached and only
# re-runs when the lockfile actually changes.
WORKDIR /app

COPY Backend/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

# ---------------------------------------------------------------------------
# Application code
# ---------------------------------------------------------------------------
COPY Backend/ /app/

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
# WhiteNoise serves these from STATIC_ROOT using
# CompressedManifestStaticFilesStorage, which must run at BUILD time - and that
# needs settings, which means it needs a SECRET_KEY.
#
# settings.py raises ImproperlyConfigured without one when DEBUG is off, so the
# build supplies a throwaway value. It is scoped to this single RUN: it is never
# an ENV, never baked into a layer, and never used at runtime, where Render's
# real SECRET_KEY is injected.
#
# --noinput because there is no TTY in a build.
RUN SECRET_KEY=build-time-placeholder-not-a-real-secret \
    ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput

# ---------------------------------------------------------------------------
# Non-root user
# ---------------------------------------------------------------------------
# Runs the app as an unprivileged user. Also gives MEDIA_ROOT a known owner
# rather than leaving root-owned files behind.
#
# (If CLOUDINARY_URL is unset, media lands on local disk - see tasks.md 2.3.0,
# where that is flagged, because in a container local disk is ephemeral.)
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
 && mkdir -p /app/media \
 && chown -R appuser:appuser /app

USER appuser

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
# Written in Python rather than curl, because `curl` is not present in a slim
# image and installing it purely to poll a health endpoint is waste.
#
# /api/v1/health/ is a plain function view with no auth and no database access,
# so it stays meaningful while the app is otherwise degraded. The port is read
# from $PORT rather than hardcoded, so the check cannot drift from the port
# gunicorn actually binds.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; \
url='http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/v1/health/'; \
sys.exit(0 if urllib.request.urlopen(url, timeout=4).status == 200 else 1)"]

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
# `exec` so gunicorn becomes PID 1 and receives SIGTERM directly. Without it,
# sh stays PID 1, swallows the signal, and Render's graceful shutdown turns into
# a SIGKILL after the grace period - which is how you get half-written requests.
#
# --workers 1 --threads 4: WeasyPrint is memory-hungry and the free tier is
# 512 MB. One worker with threads is the safe default, not a throughput
# recommendation. Re-tune only against measured numbers.
#
# --timeout 90: generous enough for a 150-line invoice on a cold instance.
#
# --worker-tmp-dir /dev/shm: gunicorn writes its heartbeat to a temp file. On a
# container the default location may be a slow disk-backed tmpfs; /dev/shm is
# memory-backed. Only tiny files are written here, so the small default size is
# not a concern.
#
# NO `migrate` HERE - see the header.
CMD ["sh", "-c", "exec gunicorn config.wsgi:application \
 --bind 0.0.0.0:${PORT:-8000} \
 --workers 1 --threads 4 \
 --timeout 90 \
 --worker-tmp-dir /dev/shm \
 --access-logfile - --error-logfile -"]