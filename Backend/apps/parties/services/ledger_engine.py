"""
Party Ledger Engine (3.2).

Computes a running-balance statement for one party by merging:

* Sales invoices issued to the party  (debit side — the party owes us)
* Payments received from the party    (credit side — we received money)

Entries are sorted chronologically and folded into a running balance that
starts from `party.opening_balance_signed` (a `Decimal`, never `float`).

Design rules:

1. **Decimals everywhere.** Money never becomes `float`, not even temporarily.
2. **Walk-in excluded.** `Party.is_walk_in` parties are excluded from
   overdue/reminder lists and "top customers" — the caller filters them out.
   This engine simply returns what it's asked for.
3. **Credit/debit notes are ledger entries too** (2.4). When credit/debit
   notes exist, they appear here as additional debit/credit rows. For now
   (2.4 not yet built), only invoices and payments are returned.
4. **Tenant-scoped.** All queries filter by `party.business` implicitly
   because the party is loaded through the tenant-scoped viewset.
"""

from decimal import Decimal

from apps.invoices.models import Invoice
from apps.payments.models import PaymentRecord


def get_party_statement(party, start_date=None, end_date=None):
    """
    Return (entries, totals) for one party.

    entries: list of dicts, oldest first, each with:
        date, type, reference, debit, credit, balance (running)
    totals: dict with total_billed, total_paid, pending_balance

    Debit  = money the party owes us  (sales invoices)
    Credit = money we received (payments in)

    Running balance starts at party.opening_balance_signed:
        CREDIT opening balance  -> positive  (party owes us money carried forward)
        DEBIT opening balance   -> negative  (we owe the party money carried forward)
    """
    entries = []

    # ---- Sales invoices (debit side: party owes us) ---------------------
    invoices = Invoice.objects.filter(
        party=party,
        status=Invoice.Status.ISSUED,
    )
    if start_date:
        invoices = invoices.filter(invoice_date__gte=start_date)
    if end_date:
        invoices = invoices.filter(invoice_date__lte=end_date)

    for inv in invoices.order_by("invoice_date", "id"):
        entries.append(
            {
                "date": inv.invoice_date,
                "type": "SALES_INVOICE",
                "reference": inv.invoice_number or f"Draft #{inv.pk}",
                "debit": inv.grand_total,
                "credit": Decimal("0"),
                "balance": None,  # filled in the running-balance pass below
            }
        )

    # ---- Payments received from the party (credit side) -----------------
    payments = PaymentRecord.objects.filter(
        party=party,
        transaction_type=PaymentRecord.TransactionType.PAYMENT_IN,
    )
    if start_date:
        payments = payments.filter(payment_date__gte=start_date)
    if end_date:
        payments = payments.filter(payment_date__lte=end_date)

    for pmt in payments.order_by("payment_date", "id"):
        entries.append(
            {
                "date": pmt.payment_date,
                "type": "PAYMENT_RECEIVED",
                "reference": pmt.reference_number or str(pmt.pk),
                "debit": Decimal("0"),
                "credit": pmt.amount,
                "balance": None,
            }
        )

    # ---- Sort chronologically and compute running balance ----------------
    entries.sort(key=lambda e: (e["date"], e["type"], e["reference"]))

    running = party.opening_balance_signed  # Decimal, never float
    for entry in entries:
        running += entry["debit"] - entry["credit"]
        entry["balance"] = running

    total_billed = sum((e["debit"] for e in entries), Decimal("0"))
    total_paid = sum((e["credit"] for e in entries), Decimal("0"))

    totals = {
        "total_billed": total_billed,
        "total_paid": total_paid,
        "pending_balance": running,
    }

    return entries, totals
