import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from decimal import Decimal
from django.contrib.auth import get_user_model
from apps.parties.models import Party
from apps.inventory.models import Item
from apps.quotations.models import Quotation
from apps.invoices.services.draft import compute_totals as compute_invoice_totals
from apps.invoices.services.gst_calculator import LineInput

User = get_user_model()
user = User.objects.get(email="owner@example.com")
bp = user.business
print(f"Business state: '{bp.state_code}'")
party = Party.objects.get(business=bp, name="Customer Co")
print(f"Party state: '{party.state_code}'")
item = Item.objects.get(business=bp, name="Test Item")
print(f"Item: {item.name}, track_stock={item.track_stock}")

q = Quotation.objects.get(business=bp)
print(f"Quotation place_of_supply: '{q.place_of_supply}', supply_type: '{q.supply_type}'")
qi = q.items.first()
print(f"Line: qty={qi.quantity}, price={qi.unit_price}, rate={qi.tax_rate}, type={qi.item_type}")

lines = [
    LineInput.build(
        quantity=qi.quantity,
        unit_price=qi.unit_price,
        tax_rate=qi.tax_rate,
        line_discount=qi.line_discount,
        item_type=qi.item_type,
    )
]
print(f"LineInput: qty={lines[0].quantity}, price={lines[0].unit_price}, rate={lines[0].tax_rate}")

totals = compute_invoice_totals(
    business=bp,
    party=party,
    lines=lines,
    place_of_supply=q.place_of_supply,
    prices_include_tax=q.prices_include_tax,
    round_invoice_total=False,
)
print(f"grand_total={totals.grand_total}, supply_type={totals.supply_type}")
print(f"cgst={totals.cgst_total}, sgst={totals.sgst_total}, igst={totals.igst_total}")
for r in totals.lines:
    print(f"  line: taxable={r.taxable_value}, cgst={r.cgst_amount}, sgst={r.sgst_amount}, igst={r.igst_amount}, total={r.total_amount}")
