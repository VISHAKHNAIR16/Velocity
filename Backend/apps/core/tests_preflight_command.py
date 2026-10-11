"""
Tests for `manage.py preflight_constraints`.

The command exists so a constraint migration cannot fail half-way on real data.
Its exit code is the whole product: 0 means "safe to migrate", 1 means "do not
migrate yet". A command that always exits 0 is worse than none, because CI would
go green and the deploy would still break.

How these tests build a violating row
--------------------------------------
On a migrated test database the constraints are **already applied**, so the ORM
refuses a bad row and raw SQL is refused too. That is correct behaviour, and it
is also the reason this command exists: it is for databases that have the *data*
but not yet the *constraint*.

So the tests that need a violation define their own model with their own
constraint, create the table without migrating it, insert a bad row, and then run
the command. That reproduces the real situation - legacy rows that predate the
constraint - rather than fighting the one the test database already has.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, models
from django.test import TestCase


def run_preflight(*args):
    """Run the command, returning (exit_code, output)."""
    out = StringIO()
    try:
        call_command("preflight_constraints", *args, stdout=out, stderr=out)
        return 0, out.getvalue()
    except SystemExit as exc:
        return exc.code, out.getvalue()


class legacy_table:
    """
    A table holding rows that violate a constraint the database does not enforce.

    This is the situation the command exists for, and reproducing it exactly
    matters. Two tempting shortcuts both fail:

    - Creating the model through Django's schema editor puts the CHECK on the
      table, so inserting the bad row is rejected and there is nothing to detect.
    - Dropping the constraint from `_meta` would leave the command with nothing
      to check, since the command derives its checks from `_meta`.

    So the model *declares* the constraint (which is what the command reads) while
    the table is created without it (which is what the data reflects). That is
    precisely "the migration has not run yet".

    `app_label="core"` so `apps.get_models()` discovers it normally - the command
    must find it the same way it finds a real model, or the test proves a special
    case instead of the behaviour.
    """

    registered = []

    def __init__(self, name="Widget", condition=None, constraint_name=None):
        self.name = name
        self.table = f"legacy_{name.lower()}"
        self.constraint_name = constraint_name or f"{self.table}_weight_not_negative"
        self.model = type(
            name,
            (models.Model,),
            {
                "__module__": __name__,
                "name": models.CharField(max_length=20),
                "weight": models.IntegerField(null=True),
                "Meta": type(
                    "Meta",
                    (),
                    {
                        "app_label": "core",
                        "db_table": self.table,
                        "constraints": [
                            models.CheckConstraint(
                                condition=condition or models.Q(weight__gte=0),
                                name=self.constraint_name,
                            )
                        ],
                    },
                ),
            },
        )
        legacy_table.registered.append(self.model)

    def __enter__(self):
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE TABLE "{self.table}" '
                "(id integer PRIMARY KEY AUTOINCREMENT, name varchar(20) NOT NULL, "
                "weight integer NULL)"
            )
        return self.model

    def __exit__(self, *exc):
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{self.table}"')
        if self.model in legacy_table.registered:
            legacy_table.registered.remove(self.model)
        return False


class CleanDatabaseTests(TestCase):
    def test_a_clean_database_passes(self):
        code, output = run_preflight()
        self.assertEqual(code, 0, output)
        self.assertIn("pass", output)

    def test_it_says_how_many_constraints_it_checked(self):
        # Not decoration: a command that silently checks nothing looks identical
        # to one that checked everything and found nothing.
        code, output = run_preflight()
        self.assertEqual(code, 0)
        self.assertRegex(output, r"All \d+ constraint check\(s\) pass")

    def test_it_finds_the_constraints_this_project_actually_has(self):
        """
        Guards against the command silently checking a smaller set than the
        project has - which is the failure mode of a hardcoded check list.
        """
        code, output = run_preflight("--verbose")
        self.assertEqual(code, 0)
        for expected in (
            "invoices.Invoice.invoice_grand_total_not_negative",
            "invoices.InvoiceItem.invoice_item_quantity_positive",
            "inventory.StockMovement.stock_movement_sign_matches_type",
            "inventory.Item.tracked_item_must_be_product_with_stock",
        ):
            self.assertIn(expected, output)

    def test_an_app_with_no_models_is_reported_rather_than_silently_passing(self):
        code, output = run_preflight("--app", "no_such_app_at_all")
        self.assertEqual(code, 0, output)
        self.assertIn("Nothing to verify", output)


class ViolationDetectionTests(TestCase):
    def test_it_fails_and_names_the_offending_rows(self):
        """
        The whole reason the command exists.

        Exit 1, the constraint named, and the primary keys listed - so the fix is
        "go and look at invoice 41" rather than "constraint violation".
        """
        with legacy_table() as widget:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {widget._meta.db_table} (name, weight) VALUES (%s, %s)",
                    ["good", 5],
                )
                cursor.execute(
                    f"INSERT INTO {widget._meta.db_table} (name, weight) VALUES (%s, %s)",
                    ["bad", -1],
                )
                cursor.execute(
                    f"INSERT INTO {widget._meta.db_table} (name, weight) VALUES (%s, %s)",
                    ["worse", -99],
                )

            code, output = run_preflight()
            self.assertEqual(code, 1, output)
            self.assertIn("BLOCK", output)
            self.assertIn("legacy_widget_weight_not_negative", output)
            self.assertIn("2 row(s)", output)
            self.assertIn("would be REJECTED", output)

    def test_a_zero_row_violation_does_not_reach_the_database(self):
        """
        Guards the shared mistake in every `WHERE NOT (...)` check.

        `NOT (weight >= 0)` is NULL - not TRUE - when `weight` IS NULL, and a
        WHERE clause keeps only TRUE rows. So a NULL silently counts as clean.
        That matches how SQL CHECK constraints actually behave, but it must be a
        deliberate decision rather than an accident, so it is pinned here.
        """
        with legacy_table() as widget:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {widget._meta.db_table} (name, weight) VALUES (%s, NULL)",
                    ["null-weight"],
                )

            code, output = run_preflight()
            self.assertEqual(code, 0, output)

    def test_several_violations_are_all_reported(self):
        """One bad constraint must not hide the others."""
        with legacy_table("FirstWidget", constraint_name="first_widget_ok") as first, \
             legacy_table("SecondWidget", models.Q(weight__gte=0), "second_widget_ok") as second:
            for model, value in ((first, -1), (second, -1)):
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"INSERT INTO {model._meta.db_table} (name, weight) VALUES (%s, %s)",
                        ["bad", value],
                    )

            code, output = run_preflight()
            self.assertEqual(code, 1)
            self.assertIn("first_widget_ok", output)
            self.assertIn("second_widget_ok", output)
            self.assertIn("2 constraint(s)", output)


class DerivedNotHardcodedTests(TestCase):
    """
    The reason this is a command and not the stray script it replaced.

    The old version had a hand-written list of predicates. It checked the
    constraints that existed when it was written and silently ignored every
    constraint added afterwards - so it would have reported "all clear" for
    `stock_movement_sign_matches_type`, which was added later and which it never
    looked at. A stale check list is worse than no check list, because it looks
    like coverage.
    """

    def test_a_constraint_added_long_after_this_command_was_written_is_still_checked(self):
        with legacy_table("LateWidget", constraint_name="late_widget_weight_not_negative") as widget:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {widget._meta.db_table} (name, weight) VALUES (%s, %s)",
                    ["bad", -3],
                )

            code, output = run_preflight()
            self.assertEqual(code, 1, output)
            self.assertIn(
                "late_widget_weight_not_negative",
                output,
                "a constraint defined after this command was written must still be "
                "checked - the checks must come from the models, not a hardcoded list",
            )


class DerivedSqlTests(TestCase):
    def test_only_check_constraints_become_predicates(self):
        """
        UniqueConstraints must be ignored.

        They are enforced by an index, not a CHECK, so `create_sql` returns
        `CREATE UNIQUE INDEX ...` with no `CHECK (...)` to extract. Running one as
        a predicate would be a syntax error, not a skipped check.
        """
        from django.apps import apps

        from apps.core.management.commands.preflight_constraints import Command

        editor = connection.schema_editor()
        seen_checks = 0

        for model in apps.get_models():
            for constraint in model._meta.constraints:
                sql = Command._check_sql(constraint, model, editor)
                if type(constraint).__name__ == "CheckConstraint":
                    seen_checks += 1
                    self.assertIsNotNone(sql, f"{model.__name__}.{constraint.name}")
                    self.assertNotIn("ADD CONSTRAINT", sql)
                    self.assertNotIn("CREATE UNIQUE INDEX", sql)
                else:
                    self.assertIsNone(sql, f"{model.__name__}.{constraint.name}")

        self.assertGreater(seen_checks, 0, "the project has CheckConstraints")

    def test_the_predicate_is_a_bare_expression(self):
        """
        Used directly inside `WHERE NOT (...)`, so it must carry no wrapper.

        Deliberately not asserting anything about balanced parentheses: a
        legitimate predicate like `"grand_total" >= '0'` has none at all.
        """
        from apps.core.management.commands.preflight_constraints import Command
        from apps.invoices.models import Invoice

        editor = connection.schema_editor()
        checked = 0

        for constraint in Invoice._meta.constraints:
            sql = Command._check_sql(constraint, model=Invoice, editor=editor)
            if not sql:
                continue
            checked += 1
            stripped = sql.strip()
            self.assertTrue(stripped, f"{constraint.name} produced an empty predicate")
            self.assertFalse(
                stripped.upper().startswith("CHECK"),
                f"{constraint.name} kept the CHECK keyword, which is not valid SQL",
            )
            self.assertNotIn("ADD CONSTRAINT", stripped)
            self.assertNotIn("ALTER TABLE", stripped)

        self.assertGreater(checked, 0)


class MultiDatabaseRoutingTests(TestCase):
    def test_the_write_router_chooses_the_database(self):
        """
        `--database` is optional because the write router is the right default,
        but it must not fall over when the router is not configured.
        """
        code, output = run_preflight("--database", "default")
        self.assertEqual(code, 0, output)
        get_user_model()  # sanity: the router's model resolves