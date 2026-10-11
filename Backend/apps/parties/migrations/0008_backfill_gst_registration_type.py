"""
Backfill `Party.gst_registration_type` from the presence of a GSTIN.

Why this is needed
------------------
The new field defaults to `UNREGISTERED`, which is correct for a party that
genuinely has no GSTIN and wrong for every existing party that has one. Without
this step the field would contradict `Party.is_registered` (`bool(gstin)`) on
every row in the database, and any purchase logic reading the new field would
refuse input tax credit from suppliers who are plainly registered.

A composition taxpayer does hold a GSTIN, so they are set `REGULAR` here. The
distinction only matters when a purchase bill is entered, which is where the
user picks the actual type - defaulting them to REGULAR keeps their existing
bills claimable rather than silently zeroing them.

Reversible: the forward direction is a filter on `gstin`, the reverse is a
filter on the field itself, so neither needs the other as input.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Party = apps.get_model("parties", "Party")
    Party.objects.filter(gst_registration_type="UNREGISTERED", gstin__gt="").update(
        gst_registration_type="REGULAR"
    )


def unbackfill(apps, schema_editor):
    Party = apps.get_model("parties", "Party")
    Party.objects.filter(gst_registration_type="REGULAR", gstin="").update(
        gst_registration_type="UNREGISTERED"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("parties", "0007_party_gst_registration_type"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]