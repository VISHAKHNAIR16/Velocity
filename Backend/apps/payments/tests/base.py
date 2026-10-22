"""
Shared fixtures for the payments API tests.

Everything is built through the public HTTP surface using literal URLs, so a
routing mistake fails these tests rather than hiding behind `reverse()`.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.models import BusinessProfile
from apps.inventory.models import Item
from apps.invoices.models import Invoice
from apps.parties.models import Party

User = get_user_model()

#: Literal URLs, deliberately not reverse(). A routing regression must fail here.
PAYMENT_LIST_URL = "/api/v1/payments/"


def make_user(email="owner@example.com", *, state_code="36"):
    user = User.objects.create_user(email=email, password="Str0ngPass!234", first_name="Owner")
    BusinessProfile.objects.create(
        user=user,
        trade_name=f"{email} Traders",
        company_name=f"{email} Traders",
        owner_name="Owner",
        phone="9000000000",
        state_code=state_code,
        gstin="36AAAAA0000A1Z5",
        pan="AAAAA0000A",
        address_line="1 Test Street",
        city="Hyderabad",
        pincode="500001",
        invoice_number_prefix="INV",
    )
    return user


def make_party(user, name="Sharma Stores", *, state_code="36", party_type="CUSTOMER"):
    return Party.objects.create(
        business=user.business,
        name=name,
        mobile=f"9{state_code}0000000",
        state_code=state_code,
        party_type=party_type,
    )


def make_item(user, *, code="SHIRT-001", name="Cotton Shirt"):
    return Item.objects.create(
        business=user.business,
        item_code=code,
        name=name,
        item_type=Item.ItemType.PRODUCT,
        unit="PCS",
        hsn_sac_code="610510",
        sales_price=Decimal("249.50"),
        tax_rate=Decimal("5.00"),
        current_stock=Decimal("100.000"),
        low_stock_threshold=Decimal("5.000"),
        track_stock=True,
    )


def make_issued_invoice(user, party, grand_total="1000.00", number=None):
    """Create an ISSUED invoice so payments can link to it."""
    from django.utils import timezone

    invoice = Invoice.objects.create(
        business=user.business,
        party=party,
        invoice_date="2026-01-15",
        status=Invoice.Status.ISSUED,
        invoice_number=number
        or f"INV/26-27/000{Invoice.objects.filter(business=user.business).count() + 1:03d}",
        issued_at=timezone.now(),
        grand_total=Decimal(grand_total),
        paid_amount=Decimal("0.00"),
        balance_due=Decimal(grand_total),
        payment_status=Invoice.PaymentStatus.UNPAID,
    )
    return invoice


def api_client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client
