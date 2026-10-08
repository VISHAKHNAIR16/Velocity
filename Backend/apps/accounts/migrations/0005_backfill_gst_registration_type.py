"""Backfill `gst_registration_type` for existing businesses.

A business that already has a GSTIN is registered, so it must be REGULAR -
otherwise its invoices would silently show zero tax and the title
"Bill of Supply". A business with no GSTIN stays UNREGISTERED (the field
default), which is the safe direction: no tax is charged rather than tax being
charged without a GSTIN.

Kept as a separate migration from the schema change so the data step can be
undone on its own.
"""

from django.db import migrations


def backfill_registration_type(apps, schema_editor):
    BusinessProfile = apps.get_model("accounts", "BusinessProfile")
    BusinessProfile.objects.filter(gstin__gt="").exclude(
        gst_registration_type="REGULAR"
    ).update(gst_registration_type="REGULAR")


def revert_registration_type(apps, schema_editor):
    """Undo: put every business that had a GSTIN back on UNREGISTERED.

    Only safe while no invoices exist, so this is provided for completeness
    rather than as a routine rollback step.
    """
    BusinessProfile = apps.get_model("accounts", "BusinessProfile")
    BusinessProfile.objects.filter(gst_registration_type="REGULAR").update(
        gst_registration_type="UNREGISTERED"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_businessprofile_gst_registration_type_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_registration_type, revert_registration_type),
    ]