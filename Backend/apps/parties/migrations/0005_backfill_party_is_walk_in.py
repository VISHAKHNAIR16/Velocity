"""
Backfill `Party.is_walk_in` for rows created by `accounts.0006`.

Decision 21 replaces "find the walk-in by its name" with a boolean flag. This
migration marks the rows the earlier data migration created, so an existing
business keeps a working counter sale without any user action.

Two things happen here, and the order matters:

1. **Flag** the existing `Walk-in / Cash Customer` rows.
2. **Blank their stored `state_code`.** Before decision 21 the walk-in stored
   the business's state, so a business that later moved states would keep
   issuing old-state invoices. From 0004 the state is resolved from the
   business at invoice time instead, so storing one can only go stale.

Edge case handled explicitly: the new `one_walk_in_per_business` partial unique
constraint permits exactly one flagged row per business, but the old code looked
the walk-in up by *name*, so a business could in principle have accumulated more
than one (e.g. two registrations with the same business profile, or a manual
party created with the same name). We flag the oldest and **rename the rest to
something honest** rather than deleting them - they may already carry invoices,
and `Invoice.party` is PROTECT, so deleting would fail anyway.
"""

from django.db import migrations

WALK_IN_PARTY_NAME = "Walk-in / Cash Customer"
#: The exact placeholder mobile written by accounts.0006. Used only to narrow
#: the match - a user-created party that happens to share the name but has a real
#: mobile is NOT a walk-in and must be left alone.
WALK_IN_MOBILE = "9876543210"


def forwards(apps, schema_editor):
    Party = apps.get_model("parties", "Party")
    db_alias = schema_editor.connection.alias

    # Oldest first, so "the oldest is the walk-in" is deterministic.
    candidates = (
        Party.objects.using(db_alias)
        .filter(name=WALK_IN_PARTY_NAME, is_walk_in=False)
        .order_by("created_at", "pk")
    )

    flagged = 0
    renamed = 0
    seen_businesses = set()

    for party in candidates.iterator():
        # Only auto-created rows: same name AND the synthetic placeholder mobile
        # AND no GSTIN. Anything else was made by a person.
        if party.mobile != WALK_IN_MOBILE or (party.gstin or "").strip():
            continue

        if party.business_id in seen_businesses:
            # A duplicate system row. Keep it, but stop pretending it is the
            # walk-in - otherwise the unique constraint would reject the insert.
            party.name = f"{WALK_IN_PARTY_NAME} (duplicate {party.pk})"
            party.save(update_fields=["name"])
            renamed += 1
            continue

        seen_businesses.add(party.business_id)
        party.is_walk_in = True
        # Decision 21: the state is resolved from the business at invoice time.
        party.state_code = ""
        party.save(update_fields=["is_walk_in", "state_code"])
        flagged += 1

    if flagged or renamed:
        print(f"  walk-in backfill: flagged {flagged}, renamed {renamed} duplicate(s)")


def backwards(apps, schema_editor):
    """
    Restore the business state on each flagged walk-in and clear the flag.

    Best-effort and deliberately NOT restoring the old name-based lookup: this
    simply puts the rows back into a state the pre-0004 code can read.
    """
    Party = apps.get_model("parties", "Party")
    BusinessProfile = apps.get_model("accounts", "BusinessProfile")
    db_alias = schema_editor.connection.alias

    businesses = {
        b.pk: (b.state_code or "")
        for b in BusinessProfile.objects.using(db_alias).only("pk", "state_code")
    }

    count = 0
    for party in Party.objects.using(db_alias).filter(is_walk_in=True).iterator():
        party.state_code = businesses.get(party.business_id, "")
        party.is_walk_in = False
        party.save(update_fields=["is_walk_in", "state_code"])
        count += 1

    if count:
        print(f"  walk-in rollback: restored state on {count} party row(s)")


class Migration(migrations.Migration):
    dependencies = [
        # Must follow 0004: the `is_walk_in` column and the one-walk-in-per-business
        # constraint both have to exist before rows are flagged against them.
        ("parties", "0004_party_is_walk_in_party_one_walk_in_per_business"),
        ("accounts", "0006_create_walk_in_parties"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]