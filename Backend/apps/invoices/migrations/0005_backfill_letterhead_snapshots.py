"""
Backfill the 2.3 letterhead snapshots onto invoices that were already issued.

**This stamps today's bank details onto historical invoices.** That is wrong
history, and there is no way to avoid it: the values were never stored, so they
cannot be recovered. The alternative is leaving every pre-existing invoice with
an empty bank block, which is visibly broken on a document the customer already
holds. A correct-looking invoice with the right account number on it beats a
blank one, and the trade is recorded in tasks.md rather than buried here.

Only ISSUED and CANCELLED invoices are touched. A draft was never sent to
anyone, and it will snapshot correctly from the live profile when it is issued.
"""

from django.db import migrations

BANK_FIELDS = (
    "bank_account_name",
    "bank_account_number",
    "bank_ifsc",
    "bank_name",
    "bank_branch",
)


def file_url(field):
    """Absolute URL of an uploaded image, or "" when there is none."""
    if not field:
        return ""
    try:
        return field.url or ""
    except (ValueError, NotImplementedError):
        return ""


def backfill(apps, schema_editor):
    Invoice = apps.get_model("invoices", "Invoice")
    BusinessProfile = apps.get_model("accounts", "BusinessProfile")

    profiles = {b.pk: b for b in BusinessProfile.objects.all()}

    # One write per invoice: a queryset update per field would mean 7 round
    # trips per row, and these tables grow forever.
    pending = Invoice.objects.exclude(status="DRAFT").only("id", "business_id")
    for invoice in pending.iterator(chunk_size=500):
        business = profiles.get(invoice.business_id)
        if business is None:
            continue
        invoice.bank_account_name = business.bank_account_name or ""
        invoice.bank_account_number = business.bank_account_number or ""
        invoice.bank_ifsc = business.bank_ifsc or ""
        invoice.bank_name = business.bank_name or ""
        invoice.bank_branch = business.bank_branch or ""
        invoice.business_logo_url = file_url(business.logo)
        invoice.business_signature_url = file_url(business.signature)
        invoice.save(
            update_fields=[
                *BANK_FIELDS,
                "business_logo_url",
                "business_signature_url",
            ]
        )


def noop(apps, schema_editor):
    """The reverse deliberately does nothing.

    Reversing would blank out bank details that are now correct on screen. A
    no-op reverse leaves accurate data in place, which is the safer failure.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0004_invoice_bank_account_name_and_more"),
        ("accounts", "0013_businessprofile_signature"),
    ]

    operations = [migrations.RunPython(backfill, noop)]