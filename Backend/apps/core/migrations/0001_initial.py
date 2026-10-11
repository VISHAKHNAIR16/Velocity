"""
Initial migration for the core app.

`apps.core` holds shared infrastructure (the tenant base model, constants, the
project error envelope) rather than models of its own, so it had no migrations at
all - and therefore no anchor to depend on. `0002_create_django_cache_table`
needs one.

Intentionally empty: adding it rather than attaching the cache-table migration to
a real app keeps project-wide infrastructure attached to the project-wide app.
"""

from django.db import migrations