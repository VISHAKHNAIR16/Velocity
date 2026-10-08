"""
Shared fixtures for the invoice API tests.

Everything is built through the public HTTP surface using literal URLs, so a
routing mistake fails these tests rather than hiding behind `reverse()`.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.models import BusinessProfile
from apps.inventory.models import Item
from apps.parties.models import Party

User = get_user_model()

#: Literal URLs, deliberately not reverse(). A routing regression must fail here.
INVOICE_LIST_URL = "/api/v1/invoices/"
PREVIEW_URL = "/api/v1/invoices/preview/"


def detail_url(invoice) -> str:
    pk = invoice.pk if hasattr(invoice, "pk") else invoice
    return f"/api/v1/invoices/{pk}/"


def copy_url(invoice) -> str:
    pk = invoice.pk if hasattr(invoice, "pk") else invoice
    return f"/api/v1/invoices/{pk}/copy/"


def make_user(
    email="owner@example.com",
    *,
    state_code="36",
    prefix="INV",
    registration=BusinessProfile.GstRegistrationType.REGULAR,
):
    user = User.objects.create_user(
        email=email, password="Str0ngPass!234", first_name="Owner"
    )
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
        invoice_number_prefix=prefix,
        gst_registration_type=registration,
    )
    return user


def make_party(user, name="Sharma Stores", *, state_code="36", gstin=""):
    return Party.objects.create(
        business=user.business,
        name=name,
        mobile=f"9{state_code}0000000",
        state_code=state_code,
        gstin=gstin,
    )


def make_item(
    user,
    *,
    code="SHIRT-001",
    name="Cotton Shirt",
    item_type=Item.ItemType.PRODUCT,
    price="249.50",
    tax_rate="5.00",
    hsn="610510",
    unit="PCS",
):
    return Item.objects.create(
        business=user.business,
        item_code=code,
        name=name,
        item_type=item_type,
        unit=unit,
        hsn_sac_code=hsn,
        sales_price=Decimal(price),
        tax_rate=Decimal(tax_rate),
        current_stock=Decimal("100.000"),
        low_stock_threshold=Decimal("5.000"),
    )


def api_client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def line_payload(item=None, **overrides) -> dict:
    """One line. Defaults to an item-backed PRODUCT line."""
    payload = {"quantity": "2", "unit_price": "249.50", "tax_rate": "5.00"}
    if item is not None:
        payload["item"] = item.pk
    payload.update(overrides)
    return payload
