"""
Create the database cache table.

`CACHES` now points at `django.core.cache.backends.db.DatabaseCache` (decision
24) so throttle counters survive a deploy and are shared across workers. Django
normally requires a manual `manage.py createcachetable`; doing it as a migration
means a fresh database is ready with no extra step and no way to forget.

The table is intentionally left empty after a rollback. Django's
`createcachetable` creates it with `IF NOT EXISTS`, so re-applying is safe.
"""

from django.conf import settings
from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    # Uses the *live* database, not the historical model registry: the cache
    # table is not a model in this project's app registry.
    with schema_editor.connection.cursor() as cursor:
        call_command(
            "createcachetable",
            verbosity=0,
            database=schema_editor.connection.alias,
        )
        del cursor


def drop_cache_table(apps, schema_editor):
    table = settings.DATABASES["default"].get("OPTIONS", {}).get("cache_table", "django_cache")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {table}")


class Migration(migrations.Migration):
    # Depends on nothing in particular; it only needs a database. Anchored to
    # the initial app migration so it lands early in a fresh database.
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]