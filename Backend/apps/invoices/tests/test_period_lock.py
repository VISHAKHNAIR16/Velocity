"""
Decision 23: `books_locked_until` and the period rules.

Decision 17 (1.4) made backdating a UI *warning*. That is advice, and advice
does not stop a back-dated invoice landing in a GSTR-1 period already filed -
where it is silently never reported. These tests pin the server-side rule that
replaced it.

The boundary matters more than the happy path: the lock date is **inclusive**
(the last day of the filed month is refused) and the earliest allowed date is
the day *after* it. Getting either wrong in the permissive direction is exactly
the bug this rule exists to prevent, so the boundary has its own tests.
"""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import BusinessProfile
from apps.invoices.errors import IssueError
from apps.invoices.services.period_rules import (
    assert_period_open,
    earliest_allowed_date,
    today_in_business_tz,
)
from apps.invoices.tests.base import (
    INVOICE_LIST_URL,
    api_client_for,
    line_payload,
    make_item,
    make_party,
    make_user,
)
from apps.invoices.tests.test_issue_api import cancel_url, issue_url

TODAY = today_in_business_tz()


class LockDateBoundaryTests(TestCase):
    """The inclusive boundary, isolated from the HTTP layer."""

    def setUp(self):
        self.business = make_user().business
        # A date comfortably in the past so "past" tests are unambiguous.
        self.lock_date = TODAY - timedelta(days=10)

    def lock(self):
        self.business.books_locked_until = self.lock_date
        self.business.save(update_fields=["books_locked_until"])

    def test_no_lock_date_changes_nothing(self):
        """A business that never sets it is completely unaffected."""
        self.business.books_locked_until = None
        self.business.save(update_fields=["books_locked_until"])
        assert_period_open(self.business, TODAY - timedelta(days=365))

    def test_the_lock_date_itself_is_refused(self):
        """Inclusive: the last day of the filed month belongs to that month."""
        self.lock()
        with self.assertRaises(IssueError) as ctx:
            assert_period_open(self.business, self.lock_date)
        self.assertEqual(ctx.exception.code, "PERIOD_LOCKED")

    def test_one_day_after_the_lock_is_allowed(self):
        self.lock()
        assert_period_open(self.business, self.lock_date + timedelta(days=1))

    def test_one_day_before_the_lock_is_refused(self):
        self.lock()
        with self.assertRaises(IssueError) as ctx:
            assert_period_open(self.business, self.lock_date - timedelta(days=1))
        self.assertEqual(ctx.exception.code, "PERIOD_LOCKED")

    def test_the_earliest_allowed_date_is_the_day_after_the_lock(self):
        self.lock()
        self.assertEqual(earliest_allowed_date(self.business), self.lock_date + timedelta(days=1))

    def test_no_lock_means_no_earliest_date(self):
        """`None`, not today - so the UI leaves the picker alone."""
        self.business.books_locked_until = None
        self.business.save(update_fields=["books_locked_until"])
        self.assertIsNone(earliest_allowed_date(self.business))

    def test_a_future_date_is_refused_when_future_is_not_allowed(self):
        with self.assertRaises(IssueError) as ctx:
            assert_period_open(self.business, TODAY + timedelta(days=1))
        self.assertEqual(ctx.exception.code, "FUTURE_DATE")

    def test_a_future_date_is_allowed_when_future_is_permitted(self):
        """Draft-save path: preparing next month's invoice is normal."""
        assert_period_open(
            self.business, TODAY + timedelta(days=30), allow_future=True
        )

    def test_today_is_never_a_future_date(self):
        """The IST boundary: `localdate()`, not the server's clock."""
        assert_period_open(self.business, TODAY)

    def test_today_is_read_in_business_timezone(self):
        """
        `TIME_ZONE = Asia/Kolkata`. Reading the host's UTC date instead would be a
        day behind for several hours every evening, rejecting invoices the user
        legitimately dated today.
        """
        self.assertEqual(TODAY, timezone.localdate())
        # And it is genuinely IST, not the host's clock: IST is UTC+5:30, so at
        # any moment where the host is on UTC the two dates differ for part of
        # the day. This asserts we are not using a naive `date.today()`.
        self.assertEqual(
            str(timezone.get_current_timezone()),
            "Asia/Kolkata",
            "the period rules must read the date in the configured timezone",
        )


class PeriodLockApiTests(TestCase):
    """The same rules over the public API, where the user actually meets them."""

    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)
        self.lock_date = TODAY - timedelta(days=10)
        self.user.business.books_locked_until = self.lock_date
        self.user.business.save(update_fields=["books_locked_until"])

    def draft(self, invoice_date):
        return self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "invoice_date": invoice_date.isoformat(),
                "items": [line_payload(self.item)],
            },
            format="json",
        )

    def issue(self, draft_id):
        return self.client_.post(issue_url(draft_id), {}, format="json")

    def cancel(self, invoice_id):
        return self.client_.post(
            cancel_url(invoice_id), {"reason": "period test"}, format="json"
        )

    # -- draft save ---------------------------------------------------------
    def test_saving_a_draft_in_a_locked_period_is_refused(self):
        response = self.draft(self.lock_date)
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["error"], "PERIOD_LOCKED")

    def test_saving_a_draft_after_the_lock_is_allowed(self):
        response = self.draft(self.lock_date + timedelta(days=1))
        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_a_future_draft_may_be_saved(self):
        """Preparing in advance is fine; only issuing is restricted."""
        response = self.draft(TODAY + timedelta(days=5))
        self.assertEqual(response.status_code, 201, response.content[:300])

    # -- issue --------------------------------------------------------------
    def test_issuing_a_draft_dated_in_a_locked_period_is_refused(self):
        """The gap this closes: the draft was saved before the lock was set."""
        draft = self.draft(self.lock_date + timedelta(days=1))
        self.assertEqual(draft.status_code, 201, draft.content[:300])

        # The user then locks the books up to today, retroactively.
        self.user.business.books_locked_until = TODAY
        self.user.business.save(update_fields=["books_locked_until"])

        response = self.issue(draft.json()["id"])
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["error"], "PERIOD_LOCKED")

    def test_issuing_a_future_draft_is_refused_with_future_date(self):
        draft = self.draft(TODAY + timedelta(days=5))
        self.assertEqual(draft.status_code, 201, draft.content[:300])

        response = self.issue(draft.json()["id"])
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["error"], "FUTURE_DATE")

    def test_a_refused_issue_burns_no_number(self):
        """
        The counter row may exist (created at last_number=0), but no number may
        be consumed - a gap in the series is a permanent artefact on a legal
        document.
        """
        from apps.invoices.models import InvoiceCounter

        # Save a valid draft, THEN lock the books up to today so its date falls
        # inside the locked period. This is the only way to reach the issue-time
        # refusal, because draft-save refuses locked dates too.
        draft = self.draft(TODAY)
        self.assertEqual(draft.status_code, 201, draft.content[:300])
        self.user.business.books_locked_until = TODAY
        self.user.business.save(update_fields=["books_locked_until"])

        response = self.issue(draft.json()["id"])
        self.assertEqual(response.status_code, 400)

        consumed = list(InvoiceCounter.objects.values_list("last_number", flat=True))
        self.assertTrue(
            all(value == 0 for value in consumed),
            f"a number was consumed by a refused issue: {consumed}",
        )

    # -- cancel -------------------------------------------------------------
    def test_cancelling_an_invoice_in_a_locked_period_is_refused(self):
        """A filed-period invoice must be corrected by credit note, not cancelled."""
        issued = self.issue(self.draft(self.lock_date + timedelta(days=1)).json()["id"])
        self.assertEqual(issued.status_code, 201, issued.content[:300])

        # Lock the books up to today, so this invoice's own date is now inside a
        # filed period. Cancelling it would remove it from a period already
        # reported to the tax department.
        self.user.business.books_locked_until = TODAY
        self.user.business.save(update_fields=["books_locked_until"])

        response = self.cancel(issued.json()["id"])
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["error"], "PERIOD_LOCKED")

    def test_cancelling_is_fine_when_the_period_is_open(self):
        issued = self.issue(self.draft(self.lock_date + timedelta(days=1)).json()["id"])
        self.user.business.books_locked_until = None
        self.user.business.save(update_fields=["books_locked_until"])

        response = self.cancel(issued.json()["id"])
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["status"], "CANCELLED")

    def test_an_invoice_issued_before_the_lock_can_still_be_cancelled(self):
        """
        The sanity check in the other direction: an invoice whose date is still
        *after* the lock is not frozen by it. Otherwise locking the books would
        quietly make every cancellation impossible, which is not the rule.
        """
        issued = self.issue(self.draft(self.lock_date + timedelta(days=1)).json()["id"])
        self.assertEqual(issued.status_code, 201)

        self.user.business.books_locked_until = self.lock_date
        self.user.business.save(update_fields=["books_locked_until"])

        response = self.cancel(issued.json()["id"])
        self.assertEqual(response.status_code, 200, response.content[:300])


class LockDateApiTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)

    def test_the_setting_round_trips_through_the_profile_api(self):
        target = date(2026, 6, 30)
        response = self.client_.patch(
            "/api/v1/business/profile/",
            {"books_locked_until": target.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.user.business.refresh_from_db()
        self.assertEqual(self.user.business.books_locked_until, target)

    def test_it_is_nullable_and_has_no_default(self):
        """
        Existing businesses must be UNLOCKED after the migration, so the column
        must be nullable and must not carry a default that fills it in. (The
        field's `default` is `NOT_PROVIDED`, not `None` - NULL comes from
        `null=True` at the database level.)
        """
        field = BusinessProfile._meta.get_field("books_locked_until")
        self.assertTrue(field.null, "books_locked_until must be nullable")
        self.assertFalse(
            field.has_default(),
            "a default would silently lock every existing business's books",
        )
        self.assertIsNone(make_user("fresh@example.com").business.books_locked_until)

    def test_garbage_is_rejected(self):
        response = self.client_.patch(
            "/api/v1/business/profile/",
            {"books_locked_until": "not-a-date"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_the_preview_tells_the_form_its_earliest_allowed_date(self):
        """
        The billing form needs to know the floor before the user submits, not
        after they get a 400.
        """
        lock_date = date(2026, 6, 30)
        self.user.business.books_locked_until = lock_date
        self.user.business.save(update_fields=["books_locked_until"])

        party = make_party(self.user)
        item = make_item(self.user)
        response = self.client_.post(
            "/api/v1/invoices/preview/",
            {"party": party.pk, "items": [line_payload(item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(
            response.json()["earliest_allowed_date"],
            (lock_date + timedelta(days=1)).isoformat(),
        )

    def test_the_preview_reports_null_when_nothing_is_locked(self):
        party = make_party(self.user)
        item = make_item(self.user)
        response = self.client_.post(
            "/api/v1/invoices/preview/",
            {"party": party.pk, "items": [line_payload(item)]},
            format="json",
        )
        self.assertIsNone(response.json()["earliest_allowed_date"])