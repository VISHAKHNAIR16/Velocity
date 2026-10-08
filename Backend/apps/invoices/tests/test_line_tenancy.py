"""
Tenant denormalisation on invoice lines (step C close-out, spec 1.4.3).

`InvoiceItem.business` is redundant - the line already reaches its tenant
through `invoice.business`. It exists so stock queries and reports never need a
join. The cost of that redundancy is that it can silently disagree, so this
module pins the invariant: **a line's business is always its invoice's business**,
on every write path.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import BusinessProfile
from apps.inventory.models import Item
from apps.invoices.models import Invoice, InvoiceItem

from .base import (
    INVOICE_LIST_URL,
    api_client_for,
    detail_url,
    line_payload,
    make_item,
    make_party,
    make_user,
)


class LineBusinessMatchesInvoiceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)

    def test_create_sets_line_business_from_the_invoice(self):
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [
                    line_payload(self.item),
                    {
                        "item_name": "Ad-hoc",
                        "item_type": Item.ItemType.SERVICE,
                        "hsn_sac_code": "996511",
                        "unit": "NOS",
                        "service_description": "Freight",
                        "quantity": "1",
                        "unit_price": "50.00",
                    },
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        invoice = Invoice.objects.get(pk=response.json()["id"])
        for line in invoice.items.all():
            self.assertEqual(
                line.business_id,
                invoice.business_id,
                f"{line.item_name}: line business {line.business_id} != "
                f"invoice business {invoice.business_id}",
            )

    def test_update_keeps_line_business_in_step_with_the_invoice(self):
        created = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()

        self.client_.put(
            detail_url(created["id"]),
            {
                "party": self.party.pk,
                "items": [line_payload(self.item, quantity="5"), line_payload(self.item, quantity="1")],
            },
            format="json",
        )
        invoice = Invoice.objects.get(pk=created["id"])
        self.assertEqual(invoice.items.count(), 2)
        for line in invoice.items.all():
            self.assertEqual(line.business_id, invoice.business_id)

    def test_copy_keeps_line_business_in_step_with_the_new_invoice(self):
        created = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()
        response = self.client_.post(
            f"/api/v1/invoices/{created['id']}/copy/", {}, format="json"
        )
        self.assertEqual(response.status_code, 201)

        copy = Invoice.objects.get(pk=response.json()["id"])
        for line in copy.items.all():
            self.assertEqual(line.business_id, copy.business_id)

    def test_a_line_cannot_be_pointed_at_another_business(self):
        """The service refuses cross-tenant items; the DB backstop is separate."""
        rival = make_user("rival@example.com", state_code="29")
        rival_item = make_item(rival, code="RIVAL-1", name="Rival goods")

        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [
                    {**line_payload(self.item), "item": rival_item.pk},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(InvoiceItem.objects.count(), 0)

    def test_line_business_is_not_settable_from_the_request(self):
        """
        `business` is not a serializer field at all, so there is nothing for a
        client to post - not even on a free-text line.
        """
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "business": make_user("third@example.com", state_code="07").business.pk,
                "items": [
                    {
                        "item_name": "Free text",
                        "item_type": Item.ItemType.SERVICE,
                        "hsn_sac_code": "996511",
                        "unit": "NOS",
                        "service_description": "Freight",
                        "quantity": "1",
                        "unit_price": "50.00",
                        "business": 99999,
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        invoice = Invoice.objects.get(pk=response.json()["id"])
        self.assertEqual(invoice.business, self.user.business)
        line = invoice.items.get()
        self.assertEqual(line.business_id, invoice.business_id)
        self.assertNotEqual(line.business_id, 99999)


class LineSelfDescribingConstraintTests(TestCase):
    """The DB backstop behind the friendly serializer validation."""

    def setUp(self):
        self.user = make_user()
        self.party = make_party(self.user)
        self.invoice = Invoice.objects.create(
            business=self.user.business, party=self.party, invoice_date="2026-04-01"
        )

    def test_constraint_rejects_a_bare_line(self):
        """A line with neither an item nor a description cannot exist."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InvoiceItem.objects.create(
                    invoice=self.invoice,
                    business=self.user.business,
                    item_name="Mystery charge",
                    item_type=Item.ItemType.SERVICE,
                    hsn_sac_code="",
                    unit="NOS",
                    quantity=Decimal("1.000"),
                    unit_price=Decimal("10.00"),
                    tax_rate=Decimal("18.00"),
                )

    def test_constraint_rejects_an_incomplete_self_describing_line(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InvoiceItem.objects.create(
                    invoice=self.invoice,
                    business=self.user.business,
                    item_name="Has a name",
                    item_type=Item.ItemType.PRODUCT,
                    hsn_sac_code="610510",
                    unit="",  # missing
                    quantity=Decimal("1.000"),
                    unit_price=Decimal("10.00"),
                    tax_rate=Decimal("5.00"),
                )

    def test_constraint_allows_a_complete_free_text_line(self):
        line = InvoiceItem.objects.create(
            invoice=self.invoice,
            business=self.user.business,
            item_name="Packing",
            item_type=Item.ItemType.SERVICE,
            hsn_sac_code="996511",
            unit="NOS",
            service_description="Cartons",
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            tax_rate=Decimal("18.00"),
        )
        self.assertIsNone(line.item_id)
        self.assertEqual(line.business_id, self.invoice.business_id)

    def test_business_profile_invoice_number_prefix_is_where_numbering_starts(self):
        """
        Sanity check on the source of the prefix used in step D.
        """
        self.assertEqual(self.user.business.invoice_number_prefix, "INV")
        self.assertIsInstance(self.user.business, BusinessProfile)
