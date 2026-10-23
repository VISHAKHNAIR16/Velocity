# QA Checklist — manual verification in a real browser

**Status: NOT SIGNED OFF.** Nothing in this file has been run. There is no browser
or Playwright runner in the development environment, so the 1.4.5 and 2.1.5
"Done when" lines in `tasks.md` remain unticked.

This file is the checklist, not a record of a run. Tick boxes only for things you
have actually seen happen in a browser — a ticked box nobody can reproduce is
worse than an empty one, because it stops the next person from checking.

## How to run it

Start the stack, then work through the sections in order. Later sections assume
the data earlier sections created.

```
cd Backend
uv run python manage.py migrate
uv run python manage.py runserver
```

Serve `Web_Frontend/` from any static server on another port (the pages call the
API cross-origin, so `CORS_ALLOWED_ORIGINS` must include it):

```
cd Web_Frontend
npx serve -l 3000
```

Log in at `http://localhost:3000/auth/login.html`.

Check the browser console on **every** page. A clean run means the console stays
empty; a red line that a user would never see still counts as a failure, because
it usually means a request is failing silently.

---

## 1. Billing (carried over from 1.4.5, still unsigned)

- [ ] Walk-in sale (no party selected) — saves, issues, prints
- [ ] Inter-state B2B with a GSTIN — CGST+SGST vs IGST splits correctly
- [ ] Free-text transport line — no HSN demanded, still on the invoice
- [ ] Inclusive-price invoice — back-calculation is right, no double tax
- [ ] Draft → issue → cancel → copy
- [ ] Print preview — layout intact at A4
- [ ] Cold start: first request after a few idle minutes is slow but succeeds
- [ ] Throttle: burst the preview and confirm the "retrying in N s" message
      appears and the totals return on their own. **The totals must never be left
      blank or stale** — that is the specific failure this guards against.
- [ ] Keyboard-only billing: tab through, add a line, issue, without a mouse

## 2. Locked periods (2.0.4)

- [ ] Set "Lock books up to" to a date inside the current month
- [ ] Billing form's date picker's earliest date becomes the next day
- [ ] An invoice dated on or before the lock date is refused, and the message
      mentions issuing a credit note
- [ ] A future-dated **draft** can still be saved
- [ ] **Issuing** that future-dated draft is refused with a future-date message
- [ ] An already-issued invoice dated inside the locked period cannot be
      cancelled

## 3. Stock tracking (2.0.6)

- [ ] Adding a product shows the "Track stock" checkbox, on by default
- [ ] Unchecking it hides the stock inputs and the stock column shows "—"
- [ ] Editing a tracked product: the quantity box is **read-only** and says why
- [ ] `Adjust Stock` and `Stock History` buttons appear instead
- [ ] A product whose unit has ledger history cannot change unit, and the page
      explains that changing it would reinterpret past quantities

## 4. Stock ledger (2.1.4) — the core of this round

Set the profile's negative-stock policy to **BLOCK** first.

### 4a. Selling stock

- [ ] Give a tracked product exactly 1 unit of opening stock
- [ ] Invoice it for 1 → succeeds; the product now shows 0
- [ ] Try to invoice it for 1 again → refused with **INSUFFICIENT_STOCK**
- [ ] The **offending line is highlighted**, and the message says how much is
      short — not just that "something is wrong"
- [ ] The invoice is still a **DRAFT** afterwards; the number was not consumed
- [ ] The invoice list shows no gap in the number series

### 4b. The two-tab race

This is the one that cannot be tested any other way.

- [ ] Open the same item in two browser tabs, both logged in as the same user
- [ ] Both tabs try to sell the last unit
- [ ] Exactly **one** succeeds. The other is refused.
- [ ] Stock shows **0**, never −1
- [ ] `Stock History` shows exactly one `SALE` row for it

### 4c. Cancelling

- [ ] Cancel the issued invoice → stock returns to its previous value
- [ ] Cancel it a second time → refused; stock does **not** return twice
- [ ] `Stock History` shows the original sale **and** the reversal. Both rows
      stay: the ledger is append-only, so a cancellation is recorded, not erased.

### 4d. Adjustments

- [ ] Adjust → "Change by" −2 → the balance drops by 2
- [ ] Adjust → "Set to" 50 → the balance becomes exactly 50
- [ ] The before → after preview matches what actually happened
- [ ] Set a stock value that would go negative → the preview flags it in red
      before saving
- [ ] The new row appears in history with the reason and note you chose

### 4e. History

- [ ] The drawer shows type badge, signed quantity, running balance, source label
- [ ] Filtering by type and by date range works
- [ ] With more than 200 movements the footer says how many are hidden —
      a truncated list that looks complete is how you conclude a movement
      never happened

### 4f. Negative stock banner

- [ ] Switch the policy to **ALLOW**, sell more than you have
- [ ] The invoice is issued; the items page shows a banner naming how many
      items are below zero
- [ ] "Show them" filters the list to exactly those items
- [ ] An untracked service never appears in that banner

### 4g. Billing-form warnings

- [ ] On **ALLOW**, exceeding stock shows amber: *"stock will go to −2"* — the
      actual resulting number, not a vague warning
- [ ] On **BLOCK**, it shows red: *"this will be rejected"*
- [ ] The same item on three lines is checked as the **sum**; three lines that
      each look fine but are short together still warn
- [ ] A service and a free-text line show no stock badge at all

### 4h. Integrity commands

After the run above, both of these must exit 0:

```
.\dev.ps1 reconcile
.\dev.ps1 audit
```

- [ ] `reconcile_stock` reports no drift
- [ ] `audit_invoices` exits 0

## 5. Deletion safety (2.0.10)

- [ ] Deleting a business that owns invoices is refused, with a clear message
- [ ] Deleting a user who owns invoices is refused
- [ ] Closing an account deactivates rather than deletes

## 6. Purchases (Phase 4)

### 6a. Creating a purchase bill
- [ ] Select a **supplier** party (party_type = SUPPLIER or BOTH)
- [ ] Enter supplier bill number, date, place of supply, supply type (INTRA/INTER)
- [ ] Add line items with item, quantity, unit price, tax rate
- [ ] **INTRA state:** CGST + SGST each = rate/2%, IGST = 0
- [ ] **INTER state:** IGST = rate%, CGST = SGST = 0
- [ ] Save → bill is **ISSUED** immediately, stock increases via `PURCHASE` movement
- [ ] Stock history shows `PURCHASE` rows for each line

### 6b. Purchase return (debit note)
- [ ] Open an issued purchase bill
- [ ] Create a return with return number, date, reason
- [ ] Select purchase items and quantities to return (cannot exceed purchased qty)
- [ ] Save → stock decreases via `PURCHASE_RETURN` movement, tax reversed pro-rata
- [ ] Return grand total matches pro-rata calculation

### 6c. Tenant isolation
- [ ] A purchase from business A does not appear in business B's list
- [ ] Cross-tenant purchase item IDs are rejected (400)

## 7. Expenses (Phase 4)

- [ ] Select an expense category (Rent, Utilities, Salaries, etc.)
- [ ] Enter date, amount (>0), tax paid (>=0), payment mode
- [ ] Optional: reference number, notes, receipt URL (Cloudinary)
- [ ] Save → expense appears in list with total_with_tax = amount + tax_paid
- [ ] Edit and delete expenses (expenses are editable, unlike payments)
- [ ] **Summary endpoint:** totals by category and by month render correctly
- [ ] Tenant isolation: another business's expenses never appear

## 8. Quotations & Estimates (Phase 4)

- [ ] Select a **customer** party (party_type = CUSTOMER or BOTH)
- [ ] Enter quote date, valid-until (default 30 days), notes, terms
- [ ] Add line items with GST calculation
- [ ] Save → quotation is **DRAFT**
- [ ] **Send:** status changes to SENT
- [ ] **Convert to Invoice:** one-click POST creates a DRAFT Invoice, quotation becomes CONVERTED
- [ ] Cannot convert an already-converted or expired quotation (409)
- [ ] Cannot edit/delete a non-DRAFT quotation

## 9. GSTR Reports (Phase 4)

- [ ] Select a tax period (YYYY-MM) and report type (GSTR-1 / GSTR-3B)
- [ ] **GSTR-1:** B2B (registered parties), B2C Large (inter-state unregistered > ₹1 lakh), B2C Small, HSN summary
- [ ] **GSTR-3B:** Outward supplies, input tax credit (from purchases), net tax liability
- [ ] **Export CSV:** downloads a .csv file with correct filename
- [ ] **Export Excel:** downloads a .xlsx file (openpyxl) with correct filename
- [ ] Empty period shows zero totals, no crash
## 10. Payments (carried over from 3.1, still unsigned)

- [ ] Record a Payment In against an issued invoice
- [ ] Invoice status updates UNPAID → PARTIAL → PAID
- [ ] Record a Payment Out to a supplier
- [ ] Payments are append-only: no edit/delete after creation

## 11. Invoice PDF (2.3)

> Requires the engine. Run `python manage.py check_pdf_engine` on the server first -
> if it reports UNAVAILABLE, none of this will work and the host needs Pango.

### 11a. The picker

- [ ] An **issued** invoice shows **Download PDF** with a template dropdown
- [ ] A **draft** shows no download button at all
- [ ] Picking **Classic** downloads a bordered, formal layout
- [ ] Picking **Modern** downloads a clean, rule-only layout - visibly different
- [ ] The two downloads agree on **every number**, including the amount in words
- [ ] The filename carries the invoice number and the template name
- [ ] The invoice list row has a PDF action on issued invoices

### 11b. The document itself

- [ ] The grand total shows **`₹`** - not a box, not a blank
- [ ] GSTIN, address and place of supply (name **and** code) are present
- [ ] The amount in words matches the grand total
- [ ] The line table header says **CGST / SGST / IGST** separately, in that order
- [ ] "Tax payable on reverse charge: No" appears
- [ ] Bank details appear **only** if they were filled in
- [ ] The signature area is drawn **even with no signature uploaded** - blank, not missing
- [ ] A logo appears if one was uploaded

### 11c. The guarantee that matters

- [ ] Note the bank account number on a business, issue an invoice, download it
- [ ] **Change** the bank account number on the business profile
- [ ] Download the **same already-issued** invoice again
- [ ] It still shows the **original** number. It must not rewrite history.
- [ ] Rename the customer, download again - the old name is still there
- [ ] Change an item's rate, download again - the old rate is still there

### 11d. Long documents

- [ ] Open a draft with about **150 lines**, issue it, download it
- [ ] The **column headings repeat** on page 2 onward
- [ ] The grand total is **not split** across a page break
- [ ] File size is sane (a 150-line invoice should be well under 1 MB)

### 11e. Hostile input

- [ ] Rename a customer to `<script>alert(1)</script>` and issue - the PDF shows
      the text, and nothing executes
- [ ] A customer name of **50+ characters** does not break the table

---

## Known gaps — do not tick these

- [ ] **Multiple logins (2.0.11):** the backend issues, lists and revokes
      sessions, but there is **no frontend** for viewing or revoking devices. The
      user cannot see their sessions or sign one out from the UI.
- **Unit conversion (dozen ↔ piece) is out of scope for 2.1** by decision 37.
  Selling in a unit other than the item's stock unit is not converted and is not
  checked — do not test it expecting it to work.
- **Reverse charge is not implemented** (deferred to 2.4). A bill from an unregistered or
  composition supplier cannot carry reverse-charge tax.
- **`itc_eligible` is recorded but nothing uses it yet.** It exists so 4.2 can total claimable credit.