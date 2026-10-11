"""
Backfill an OPENING movement for every tracked item that already has a quantity.

Decision 35 (cutover). Stock counting begins when the ledger is installed.
Invoices issued **before** this migration never deducted and never will - so the
starting balance must be recorded explicitly, once, as the truth it was at the
moment the ledger went live.

Without this the ledger would sum to zero while `Item.current_stock` said 25,
`reconcile_stock` would fail on day one, and the shop would have no way to tell
whether the ledger or the counter was wrong.

`source_type = SYSTEM` with no `source_line_id`, so it is not treated as a
transaction and cannot collide with a real invoice line's idempotency slot.
"""

from django.db import migrations


def write_opening_balances(apps, schema_editor):
    Item = apps.get_model("inventory", "Item")
    StockMovement = apps.get_model("inventory", "StockMovement")
    db_alias = schema_editor.connection.alias

    tracked = (
        Item.objects.using(db_alias)
        .filter(track_stock=True)
        .exclude(current_stock=None)
        .exclude(current_stock=0)
        .order_by("id")
    )

    written = 0
    for item in tracked.iterator():
        StockMovement.objects.using(db_alias).create(
            business_id=item.business_id,
            item_id=item.pk,
            movement_type="OPENING",
            quantity_change=item.current_stock,
            stock_after=item.current_stock,
            source_type="SYSTEM",
            source_label="STOCK LEDGER CUTOVER",
            reason="COUNT_CORRECTION",
            note="Opening balance at stock-ledger cutover",
        )
        written += 1

    if written:
        print(f"  stock ledger: wrote {written} OPENING movement(s)")


def remove_opening_balances(apps, schema_editor):
    """Reverse: delete only the rows this migration wrote."""
    StockMovement = apps.get_model("inventory", "StockMovement")
    db_alias = schema_editor.connection.alias
    StockMovement.objects.using(db_alias).filter(
        source_type="SYSTEM", source_label="STOCK LEDGER CUTOVER"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0007_stockmovement"),
    ]

    operations = [
        migrations.RunPython(write_opening_balances, remove_opening_balances),
    ]