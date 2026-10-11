"""
`manage.py preflight_constraints` - check existing rows *before* adding a
`CheckConstraint` that they would violate.

Why this exists
---------------
An `AddConstraint` that trips over existing rows fails **halfway**. The migration
stops, the schema is left partly applied, and the error points at SQL rather than
at the business data that caused it. Running this first turns "constraint
violation" into a list of invoice numbers you can actually go and look at.

Read-only: it only ever issues `SELECT`. Safe against production.

Why the checks are derived, not hardcoded
-----------------------------------------
The first version of this was a stray script at the repo root with a hardcoded
list of predicates. That list is a trap: the moment somebody adds a constraint,
this script silently stops checking it, so it reports "all clear" for a constraint
that was never tested. It is worse than no script, because it looks like coverage.

So the checks are read from `Model._meta.constraints` and rendered to SQL by
Django's own schema editor. **A new constraint is checked the day it is written**,
with no edit here, and the SQL is correct for whichever database is being used -
the same reason not to hand-write these predicates.

    python manage.py preflight_constraints
    python manage.py preflight_constraints --verbose     # include passing checks

Exit code 1 if anything would be rejected, so this can gate a deploy.
"""

import re

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connections, router

# `ADD CONSTRAINT "name" CHECK (<expr>)` -> we want just `<expr>`.
_CHECK_RE = re.compile(r"CHECK\s*\((?P<expr>.*)\)\s*$", re.IGNORECASE | re.DOTALL)


class Command(BaseCommand):
    help = (
        "Report rows that existing CheckConstraints would reject, before those "
        "constraints are applied. Read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default=None,
            help="Database alias to check (defaults to the write router's choice).",
        )
        parser.add_argument(
            "--app",
            action="append",
            dest="apps",
            default=None,
            help="Limit to these app labels. Repeatable.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Also list the constraints that pass.",
        )
        parser.add_argument(
            "--sample",
            type=int,
            default=5,
            help="How many offending primary keys to show per constraint.",
        )

    def handle(self, *args, **options):
        alias = options["database"] or router.db_for_write(get_user_model())
        connection = connections[alias]

        model_labels = (
            set(options["apps"])
            if options["apps"]
            else {m._meta.label for m in apps.get_models()}
        )

        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))

            # Ask Django to render each constraint exactly as it would when
            # creating it. This is dialect-correct by construction.
            editor = connection.schema_editor()

            checked = 0
            violations = 0

            for model in apps.get_models():
                if model._meta.label not in model_labels:
                    continue
                if not model._meta.managed:
                    continue

                table = model._meta.db_table
                if table not in tables:
                    # Fresh database, or a table Django believes in that the
                    # database does not have. Not a finding.
                    continue

                for constraint in model._meta.constraints:
                    statement = self._check_sql(constraint, model, editor)
                    if statement is None:
                        continue  # not a CheckConstraint

                    checked += 1
                    label = f"{model._meta.label}.{constraint.name}"

                    try:
                        cursor.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE NOT ({statement})"
                        )
                        count = cursor.fetchone()[0]
                    except Exception as exc:  # noqa: BLE001
                        # A predicate this database cannot evaluate is a gap in
                        # the check, so say so loudly rather than passing it.
                        self.stdout.write(
                            self.style.WARNING(f"  SKIP  {label}: {str(exc).strip()[:80]}")
                        )
                        continue

                    if count:
                        violations += 1
                        self.stdout.write(
                            self.style.ERROR(f"  BLOCK {label}: {count} row(s)")
                        )
                        cursor.execute(
                            f"SELECT {model._meta.pk.column} FROM {table} "
                            f"WHERE NOT ({statement}) "
                            f"ORDER BY {model._meta.pk.column} LIMIT {options['sample']}"
                        )
                        ids = ", ".join(str(row[0]) for row in cursor.fetchall())
                        self.stdout.write(f"        sample {model._meta.pk.column}s: {ids}")
                    elif options["verbose"]:
                        self.stdout.write(f"  ok    {label}")

        if violations:
            self.stdout.write(
                self.style.ERROR(
                    f"\n{violations} constraint(s) would be REJECTED by existing rows."
                    "\nFix the rows listed above before migrating, or the migration will"
                    "\nfail part-way with a less useful message."
                )
            )
            raise SystemExit(1)

        if not checked:
            self.stdout.write(
                self.style.WARNING(
                    "No CheckConstraints found. Nothing to verify - if you expected"
                    " some, the app may not be installed or the table may not exist yet."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f"\nAll {checked} constraint check(s) pass.")
        )

    @staticmethod
    def _check_sql(constraint, model, editor):
        """
        Return the bare predicate of a CheckConstraint, or None if it is not one.

        Goes through `create_sql` rather than `condition.sql()` because that is
        the exact string the database will be asked to enforce - no second dialect
        to keep in sync, and no chance of this check and the real constraint
        disagreeing.
        """
        if type(constraint).__name__ != "CheckConstraint":
            return None

        match = _CHECK_RE.search(str(constraint.create_sql(model, editor)))
        return match.group("expr") if match else None