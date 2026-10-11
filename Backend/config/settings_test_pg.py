"""
Test settings that run against a REAL PostgreSQL, for the concurrency tests only.

Why this exists
---------------
Django only emits `FOR UPDATE` when the backend advertises support
(`django/db/models/sql/compiler.py`: `if self.query.select_for_update and
features.has_select_for_update`). SQLite inherits `has_select_for_update = False`
from `base/features.py`, so `select_for_update()` is **silently ignored** there.
A concurrency test on SQLite therefore passes whether or not the locking code
works — which is worse than having no test, because it hides a real bug.

Where the database comes from
-----------------------------
Set `TEST_DATABASE_URL` to a **Neon dev branch** (never the production branch):

    # Neon console -> Branches -> create a branch named e.g. `dev-test`
    # then copy its connection string into Backend/.env as:
    TEST_DATABASE_URL=postgresql://user:pass@ep-.../neondb?sslmode=require

The Neon console's default copy button gives you the *pooled* endpoint. Paste it
anyway - `_test_database_url()` below rewrites it to the direct endpoint and
explains why. Concurrency tests must not go through PgBouncer.

`DATABASE_URL` is deliberately NOT used, so a missing `TEST_DATABASE_URL` cannot
accidentally point the concurrency tests at real data. `TEST: {"NAME": ...}` is
set to its own database so it cannot collide with the fast SQLite suite.

Running
-------
    uv run python manage.py test apps.invoices.tests.test_concurrency \
        --settings=config.settings_test_pg

⚠️ Do NOT pass `--keepdb` to the full suite. Data migrations (notably
`accounts.0006_create_walk_in_parties`) run once when the test database is first
created, so a *reused* test database carries those rows into later runs and
fails isolation tests that assume a clean slate — errors that look like
tenant-isolation bugs but are not. `--keepdb` is fine for re-running a single
fast test; use a fresh database for the authoritative full-suite run.

Without `TEST_DATABASE_URL` the settings module raises, so the suite cannot
quietly pass on the wrong engine. `InvoiceConcurrencyTests` additionally skips
with a clear message if the backend still lacks `select_for_update`.
"""

import os

import dj_database_url

from .settings_test import *  # noqa: F403,F401  (in-memory SQLite base + fast hasher)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

if not TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set.\n"
        "The invoice concurrency tests must run on real PostgreSQL, because Django "
        "ignores select_for_update() on SQLite (settings_test uses in-memory "
        "SQLite), which would make those tests pass without proving anything.\n"
        "Point TEST_DATABASE_URL at a Neon DEV BRANCH in Backend/.env - never at "
        "production. See the module docstring for the full command."
    )

def _test_database_url() -> str:
    """
    Return the URL to test against, refusing to run through a connection pooler.

    `TEST_DATABASE_URL` is nearly always copied from the Neon console, and the
    console's default button hands you the **pooled** endpoint
    (`ep-xxx-pooler.region.aws.neon.tech`) - which sits behind PgBouncer in
    transaction-pooling mode. That is fine for the app and actively harmful for
    these tests, for two reasons:

    1. `SELECT ... FOR UPDATE` is only safe if the whole transaction stays on one
       server connection. Poolers reassign connections per transaction, so the
       row these tests are trying to lock may not be held by the connection that
       ends up running the next statement. The tests would then be measuring
       the pooler, not the locking.
    2. At teardown Django issues `DROP DATABASE`, and PgBouncer keeps idle
       sessions open to that database, so it fails with
       `database "test_..." is being accessed by other users`. Every test
       reports OK and the command still exits non-zero, which is a genuinely
       confusing failure.

    Neon exposes the direct endpoint under the same name with `-pooler` removed,
    so derive it rather than making people hunt for a second URL:

        ep-xxx-pooler.region.aws.neon.tech  ->  ep-xxx.region.aws.neon.tech
    """
    import re
    import sys

    url = TEST_DATABASE_URL
    host = re.search(r"@([^/:?]+)", url)
    if not host:
        return url
    hostname = host.group(1)

    if "-pooler" not in hostname:
        return url

    direct = url.replace(f"@{hostname}", f"@{hostname.replace('-pooler', '', 1)}")
    print(
        "  NOTICE: TEST_DATABASE_URL points at a pooled endpoint; rewriting to the\n"
        f"          direct endpoint ({hostname} -> "
        f"{hostname.replace('-pooler', '', 1)}).\n"
        "          Concurrency tests must not run through PgBouncer (see\n"
        "          config/settings_test_pg.py::_test_database_url).",
        file=sys.stderr,
    )
    return direct


DATABASES = {
    "default": dj_database_url.parse(
        _test_database_url(),
        conn_max_age=0,  # never pool across the threaded concurrency tests
    )
}
DATABASES["default"]["TEST"] = {
    "NAME": os.getenv("TEST_DATABASE_NAME", "test_velocity_invoices"),
}

# The threaded tests need real connections; SQLite's shared in-memory database
# cannot support TransactionTestCase across threads at all.
DATABASES["default"]["ENGINE"] = "django.db.backends.postgresql"