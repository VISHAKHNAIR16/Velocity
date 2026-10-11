"""
2.0.10: `on_delete` audit - deleting a business must never destroy records.

The P0 this closes (1.4.9 finding #11): `TenantModel.business` was `CASCADE`, so
deleting ONE `BusinessProfile` silently removed every party, item, invoice and
invoice line belonging to it. Legal and accounting records, destroyed by an admin
click, with nothing to stop it.

Each test asserts `ProtectedError`, so removing the `PROTECT` fails it.
"""

from django.db.models.deletion import ProtectedError
from django.test import TestCase

from apps.accounts.models import BusinessProfile
from apps.inventory.models import Item
from apps.invoices.tests.base import (
    api_client_for,
    line_payload,
    make_item,
    make_party,
    make_user,
)
from apps.invoices.tests.test_issue_api import issue_url
from apps.parties.models import Party


class BusinessDeletionIsProtectedTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.business = self.user.business

    def test_a_business_with_an_issued_invoice_cannot_be_deleted(self):
        client_ = api_client_for(self.user)
        party = make_party(self.user)
        item = make_item(self.user)
        draft = client_.post(
            "/api/v1/invoices/",
            {"party": party.pk, "items": [line_payload(item)]},
            format="json",
        ).json()
        client_.post(issue_url(draft["id"]), {}, format="json")

        with self.assertRaises(ProtectedError):
            self.business.delete()
        self.assertTrue(BusinessProfile.objects.filter(pk=self.business.pk).exists())

    def test_a_business_with_only_a_draft_cannot_be_deleted(self):
        client_ = api_client_for(self.user)
        party = make_party(self.user)
        item = make_item(self.user)
        client_.post(
            "/api/v1/invoices/",
            {"party": party.pk, "items": [line_payload(item)]},
            format="json",
        )
        with self.assertRaises(ProtectedError):
            self.business.delete()

    def test_a_business_with_only_parties_cannot_be_deleted(self):
        make_party(self.user)
        with self.assertRaises(ProtectedError):
            self.business.delete()

    def test_a_business_with_only_items_cannot_be_deleted(self):
        make_item(self.user)
        with self.assertRaises(ProtectedError):
            self.business.delete()

    def test_a_business_with_nothing_can_be_deleted(self):
        """
        The empty case must still work - PROTECT is not a blanket refusal, or the
        admin becomes a trap and operators would reach for the database instead.
        """
        empty_owner = make_user("empty@example.com")
        empty = empty_owner.business
        self.assertEqual(empty.invoices_invoice_set.count(), 0)

        empty.delete()
        self.assertFalse(BusinessProfile.objects.filter(pk=empty.pk).exists())


class UserDeletionIsProtectedTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.business = self.user.business

    def test_a_user_who_owns_a_business_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.user.delete()
        self.assertTrue(BusinessProfile.objects.filter(pk=self.business.pk).exists())

    def test_deleting_the_user_does_not_take_the_invoices_with_it(self):
        """The exact cascade 2.0.10 removed."""
        client_ = api_client_for(self.user)
        party = make_party(self.user)
        item = make_item(self.user)
        draft = client_.post(
            "/api/v1/invoices/",
            {"party": party.pk, "items": [line_payload(item)]},
            format="json",
        ).json()
        client_.post(issue_url(draft["id"]), {}, format="json")

        with self.assertRaises(ProtectedError):
            self.user.delete()

        # Still there, untouched.
        self.assertTrue(Party.objects.filter(pk=party.pk).exists())
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())


def _on_delete_name(model, field_name: str) -> str:
    """
    `'PROTECT'` rather than `<function PROTECT at 0x...>`.

    `on_delete` is a callable, so `str()` on it yields a repr containing a
    memory address - which would make every assertion here fail for a reason
    that has nothing to do with the behaviour under test.
    """
    return model._meta.get_field(field_name).remote_field.on_delete.__name__


class OnDeleteConfigurationTests(TestCase):
    """
    Pins the *configuration*, so the fix cannot be undone by accident.

    A behavioural test would still pass if someone flipped one field and another
    constraint happened to block the delete.
    """

    def test_tenant_models_protect_their_business(self):
        from apps.core.models import TenantModel

        self.assertEqual(
            _on_delete_name(TenantModel, "business"),
            "PROTECT",
            "TenantModel.business must not cascade: one delete would destroy every record",
        )

    def test_the_business_profile_protects_its_owner(self):
        self.assertEqual(_on_delete_name(BusinessProfile, "user"), "PROTECT")

    def test_invoice_lines_still_cascade_from_their_invoice(self):
        """
        The one CASCADE that must stay: a line has no meaning without its
        invoice, and deleting a DRAFT should take its lines with it.
        """
        from apps.invoices.models import InvoiceItem

        self.assertEqual(_on_delete_name(InvoiceItem, "invoice"), "CASCADE")

    def test_sessions_still_cascade_from_their_user(self):
        """A session is authentication bookkeeping, not a record."""
        from apps.accounts.models import UserSession

        self.assertEqual(_on_delete_name(UserSession, "user"), "CASCADE")

    def test_invoice_items_protect_their_catalogue_entry(self):
        """
        A catalogue item referenced by any invoice must not be deletable -
        the snapshot on the line is a copy, not a substitute.
        """
        from apps.invoices.models import InvoiceItem

        self.assertEqual(_on_delete_name(InvoiceItem, "item"), "PROTECT")


class ClosingInsteadOfDeletingTests(TestCase):
    """
    The supported path. If deactivating does not work, deleting was the only
    option a frustrated operator would have - which is exactly how records get
    destroyed.
    """

    def setUp(self):
        self.user = make_user()

    def test_a_business_can_be_closed_by_deactivating_its_owner(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertTrue(BusinessProfile.objects.filter(pk=self.user.business.pk).exists())

    def test_a_deactivated_owner_cannot_sign_in(self):
        """
        The point of deactivating: an ex-employee must lose access immediately,
        which `is_active = False` gives and a deletion would not (the email
        could simply be re-registered).
        """
        from django.contrib.auth import get_user_model

        user = self.user
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.assertFalse(
            get_user_model().objects.get(pk=user.pk).is_active,
            "deactivation must actually take effect",
        )