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

## 6. Purchases (2.2)

### 6a. Entering a bill

- [ ] New Purchase opens with a **supplier** picker (suppliers only; walk-ins are excluded)
- [ ] Typing a bill number already used by that supplier this year is refused, and the message
      explains why
- [ ] The same number from a *different* supplier is accepted
- [ ] **Check Figures** compares your typed totals against the calculator
- [ ] If they agree it says so in green
- [ ] If they differ it says **"your numbers will be saved exactly as entered"** — the wording
      matters: nothing on this page may overwrite a figure you typed
- [ ] There is no control anywhere that writes the computed figures into the form

### 6b. Stock

- [ ] Posting a bill **increases** stock by the line quantities
- [ ] A freight / free-text line posts fine and moves **no** stock
- [ ] Posting twice (double-click) adds the stock **once**
- [ ] A posted bill's money fields are locked, with a reason shown

### 6c. Cancelling

- [ ] Cancelling takes the stock back
- [ ] `Stock History` shows **both** rows — the original purchase and the reversal
- [ ] Cancelling twice removes the stock once
- [ ] A reason is required
- [ ] Set the policy to **BLOCK**, sell the goods on, then try to cancel: refused, and the message
      names **which item** and what the balance would become

### 6d. Two bills, one item (concurrency)

- [ ] Open two tabs, post a bill for the same item in each
- [ ] **Both** succeed and the stock rises by the sum — goods genuinely arrived twice
- [ ] `reconcile_stock` reports no drift afterwards

### 6e. Integrity

```
.\dev.ps1 audit          # audit_purchases
.\dev.ps1 reconcile
.\dev.ps1 preflight
```

- [ ] `audit_purchases` exits 0
- [ ] Deliberately post a bill whose grand total is wrong: `audit_purchases` reports `DIVERGENCE`
      and exits 1 — but the bill still saved exactly what you typed
- [ ] A supplier's bill numbers with a **gap** (SUP-001 then SUP-900) is **not** reported. Only
      duplicates are.

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