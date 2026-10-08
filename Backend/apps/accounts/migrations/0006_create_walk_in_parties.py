"""Create a walk-in customer for every existing business.

Retail/counter sales are the majority case for a shop, and `Invoice.party` will
be a required FK. Without a walk-in customer, every cash sale would first force
the user to create a party by hand. Idempotent and safe to re-run.

Businesses whose walk-in would have no state code (state not set yet) are
skipped: the service helper backfills the state the first time it is used, so
there is no need to create a row we cannot fill correctly here.
"""

from django.db import migrations

WALK_IN_PARTY_NAME = "Walk-in / Cash Customer"
WALK_IN_MOBILE = "9876543210"


def create_walk_in_parties(apps, schema_editor):
    BusinessProfile = apps.get_model("accounts", "BusinessProfile")
    Party = apps.get_model("parties", "Party")

    for business in BusinessProfile.objects.exclude(state_code=""):
        Party.objects.get_or_create(
            business=business,
            name=WALK_IN_PARTY_NAME,
            defaults={
                "party_type": "CUSTOMER",
                "mobile": WALK_IN_MOBILE,
                "email": "",
                "gstin": "",
                "pan": "",
                "state_code": business.state_code,
            },
        )


def remove_walk_in_parties(apps, schema_editor):
    Party = apps.get_model("parties", "Party")
    Party.objects.filter(name=WALK_IN_PARTY_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_backfill_gst_registration_type"),
        ("parties", "0003_remove_party_unique_gstin_per_business_and_more"),
    ]

    operations = [
        migrations.RunPython(create_walk_in_parties, remove_walk_in_parties),
    ]