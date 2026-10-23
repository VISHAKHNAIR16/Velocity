"""
Shared fixtures for the quotations API tests.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.models import BusinessProfile
from apps.inventory.models import Item
from apps.parties.models import Party
from apps.quotations.models import Quotation, QuotationItem

User = get_user_model()

QUOTATION_LIST_URL = "/api/v1/quotations/"


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
    )
    return user


def make_party(user, name="Customer Co", *, state_code="36", party_type="CUSTOMER"):
    return Party.objects.create(
        business=user.business,
        name=name,
        mobile=f"9{state_code}0000000",
        state_code=state_code,
        party_type=party_type,
        gstin=f"{state_code}AAAAA0000A1Z5",
    )


def make_item(user, *, code="ITEM-001", name="Test Item"):
    return Item.objects.create(
        business=user.business,
        item_code=code,
        name=name,
        item_type=Item.ItemType.PRODUCT,
        unit="PCS",
        hsn_sac_code="610510",
        sales_price=Decimal("249.50"),
        tax_rate=Decimal("18.00"),
        current_stock=Decimal("100.000"),
        track_stock=True,
    )


def make_quotation(user, party, item, grand_total="1000.00", status="DRAFT"):
    quotation = Quotation.objects.create(
        business=user.business,
        party=party,
        quotation_date="2026-01-15",
        valid_until="2026-02-15",
        place_of_supply="36",
        supply_type="INTRA",
        status=status,
        grand_total=Decimal(grand_total),
        customer_name=party.name,
        customer_gstin=party.gstin or "",
        business_name=user.business.trade_name or "",
        business_gstin=user.business.gstin or "",
    )
    QuotationItem.objects.create(
        quotation=quotation,
        business=user.business,
        item=item,
        item_name=item.name,
        item_type="PRODUCT",
        hsn_sac_code=item.hsn_sac_code,
        unit=item.unit,
        quantity=Decimal("10.000"),
        unit_price=Decimal("100.00"),
        tax_rate=Decimal("18.00"),
        taxable_value=Decimal("1000.00"),
        cgst_amount=Decimal("90.00"),
        sgst_amount=Decimal("90.00"),
        igst_amount=Decimal("0.00"),
        total_amount=Decimal("1180.00"),
    )
    return quotation


def api_client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client
