# FUTURE_CHECKLIST.md: Improvements for After the Core Software Works

A living list. Add to it as we build. Work through it only after the main phases in `tasks.md` are done and tested.

Legend: [ ] not started, [x] done

---

## 1. Security

- [ ] **Login/register rate limiting.** Left out on purpose: behind Render's proxy, DRF sees one shared IP, so a limit would lock out all users together. Fix: configure `NUM_PROXIES` correctly (verify the real `X-Forwarded-For` header on Render), or throttle per email address.
- [ ] **Email verification** on registration (stops fake or mistyped emails).
- [ ] **Forgot / reset password** flow by email.
- [ ] **JWT blacklist on logout** (`rest_framework_simplejwt.token_blacklist`) so stolen refresh tokens can be revoked.
- [ ] **Move JWT from localStorage to httpOnly cookies** (removes the XSS token-theft risk). Needs same-site setup or a shared parent domain.
- [ ] **Registration enumeration:** "email already exists" reveals who has an account. Consider a generic response plus email confirmation.
- [ ] **GSTIN ownership check.** Currently only the format is validated. Verify against the GST portal API or a verification provider, and stop two accounts claiming one GSTIN.
- [ ] Raise `SECURE_HSTS_SECONDS` from 1 hour to 1 year after everything is verified.
- [ ] Change the `/admin/` URL, or restrict it by IP.
- [ ] Add a Content-Security-Policy header on the Vercel frontend.
- [ ] Optional two-factor authentication for owners.
- [ ] Run `pip-audit` / `uv` dependency audits regularly.

## 2. Data and Database

- [ ] Confirm Neon backup / point-in-time-restore retention on the plan in use. Test a restore once.
- [ ] Keep separate Neon branches: `production` and `dev`. Never develop against production.
- [ ] Soft delete (an `is_active` or `deleted_at` flag) for Parties and Items so invoices keep their history.
- [ ] Audit log: who created or changed which invoice and when.
- [ ] Financial-year-aware invoice numbering (`INV/2026-27/0001`, reset each April).
- [ ] Update `tasks.md`: quantities use 3 decimal places, money uses 2.
- [ ] Data export for users (full backup as Excel/CSV).

## 3. Business Features

- [ ] Multiple businesses per user (a user owning several firms).
- [ ] Staff users with roles (owner, accountant, billing-only).
- [ ] Cloudinary logo upload (planned with the profile screen).
- [ ] Signature and stamp image on invoices.
- [ ] Auto-fill state code from GSTIN; "Fetch Details" button via a GST lookup provider.
- [ ] E-invoicing (IRN / QR code) and e-way bill support for businesses above the turnover limit.
- [ ] GSTR-3B summary and HSN-wise summary.
- [ ] Multiple bank accounts and UPI QR code on invoices.
- [ ] Invoice templates and themes.
- [ ] Recurring invoices and payment reminders (WhatsApp/SMS/email).
- [ ] Barcode scanning for items.
- [ ] Hindi and regional language support.

## 4. Quality and Operations

- [ ] Automated tests: especially multi-tenant isolation (User A must never see User B's data), GST calculations, and stock changes.
- [ ] GitHub Actions CI: run `ruff` and tests on each push.
- [ ] Pre-commit hooks (ruff format and lint).
- [ ] Error monitoring (Sentry free tier) and structured logging.
- [ ] Uptime monitor that pings `/api/v1/health/` so Render's free tier stays awake (or move to a paid instance).
- [ ] Shared cache (Redis) if more than one server process is ever used (throttling, caching).
- [ ] OpenAPI / Swagger docs with `drf-spectacular` (needed for the Flutter app).
- [ ] Database indexes after real usage shows slow queries.
- [ ] Custom domain for frontend and backend, then tighten `ALLOWED_HOSTS` / CORS.
- [ ] Vercel preview-deployment URLs and CORS handling.

## 5. Frontend

- [ ] Replace the Tailwind CDN script with a built, minified Tailwind CSS file for production speed.
- [ ] Shared API helper with automatic token refresh on 401.
- [ ] Accessibility pass (labels, focus, contrast, keyboard use).
- [ ] Mobile-responsive review of every page.
- [ ] Installable PWA / offline billing.
- [ ] Friendly loading and error states everywhere (Render cold starts take up to a minute).

## 6. Mobile (Flutter, Phase 4)

- [ ] Reuse the same API; confirm token refresh and the error format work in the app.
- [ ] Store tokens in secure storage (Keychain / Keystore), not plain preferences.

## 7. Media and Uploads (added with the logo upload)

- [ ] Serve resized logos (Cloudinary transformations / thumbnails) on invoices and lists instead of the full-size original.
- [ ] Clean up orphaned Cloudinary files. Deleting a user or business deletes the database row but not the stored image.
- [ ] Use separate Cloudinary folders or accounts for dev and production, so test uploads don't mix with real ones.
- [ ] Enforce the upload size limit at the proxy/server level too. Right now the 2 MB limit is checked after the upload has been received.
- [ ] Re-check Cloudinary's current free-tier storage and bandwidth limits before launch.
- [ ] Add a brand-coloured placeholder logo (initials of the business) when no logo is uploaded.

## 8. Multi-Tenancy and Testing (added with the tenant base)

- [ ] Run the test suite against PostgreSQL in CI. Local tests use in-memory SQLite for speed, so Postgres-specific behaviour is not covered.
- [ ] Add PostgreSQL Row-Level Security as a second layer of tenant isolation (defence in depth if application code ever forgets a filter).
- [ ] Add query-count assertions (`assertNumQueries`) to list endpoints to catch N+1 problems early.
- [ ] Tenant data export and deletion tool (full account closure, with Cloudinary file cleanup).
- [ ] Support users belonging to more than one business (the tenant lookup is one function, `get_business()`, so this is a contained change).
- [ ] Admin: a safe "view as business" support mode with an audit trail, instead of raw cross-tenant admin access.

## 9. Phase 1.3 Review — production readiness (added after the Items catalogue)

Checked after Phase 1.3 shipped (121 tests green, ruff clean, no migration drift).
Split into *do before launch* and *someday*.

### 9a. Must settle before Phase 1.4 (these change invoice maths)

- [ ] **Define the GST rounding policy — nothing in the codebase states one yet.**
      Pick `ROUND_HALF_UP` (the Indian convention) and apply it *only at the final display/total
      level*, never per line. Without this, `18%` of `₹33.33` has two defensible answers and the
      JS preview will disagree with the Python total. Set it once in a shared module
      (next to `apps/core/constants.py`) and use it in both the calculator and the PDF.
- [ ] **`price_includes_tax` is stored but never used.** Decide and implement the rule: if the flag
      is on, tax is *extracted* from the price (`tax = price × rate / (100 + rate)`, taxable value
      `= price − tax`) instead of added on top. Getting this backwards silently over-charges by
      ~18% on every tax-inclusive invoice, and the two modes must not be mixed on one invoice.
- [ ] **Invoice numbering must be concurrency-safe and per business.** `AUTO_INCREMENT` alone
      leaks gaps across tenants and races under two simultaneous invoices. Use a per-business
      counter row locked with `select_for_update()` inside the same `@transaction.atomic` block
      that writes the invoice, plus a `UniqueConstraint(business, invoice_number)` so a duplicate
      can never be committed.
- [ ] **Decide the `Invoice` → `Item` FK `on_delete` behaviour while there is no data yet.**
      It is cheap to change before invoices exist and expensive afterwards. Recommended:
      `on_delete=models.PROTECT` plus the existing soft-delete flag, so an item referenced by any
      invoice can never be deleted out from under it.
- [ ] **Confirm the business-state gate rejects invoices, not the profile** (decision taken, listed
      under 1.4.2 in `tasks.md`) and add the matching UI warning on the billing screen so the user
      is not blocked at submit time with no warning.

### 9b. Before production launch

- [ ] **Add `backend/.env.example`.** *(done during this review — verify it is committed)* Every
      setting `config/settings.py` reads is documented there: `DEBUG`, `SECRET_KEY`,
      `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`,
      `CLOUDINARY_URL`.
- [ ] **Wire `ruff check` into CI.** `ruff` is already configured in `pyproject.toml`
      (`E,F,I,B,UP`, line length 100) and currently passes, but nothing enforces it, so the next
      contributor can reintroduce unused imports and unsorted blocks silently.
- [ ] **Run the test suite against PostgreSQL at least once.** Local runs use in-memory SQLite;
      production is Neon Postgres. Partial unique indexes and `select_for_update()` behave
      differently, and Phase 1.4 depends on the latter.
- [ ] **Add `assertNumQueries` to the list endpoints** once invoices exist (see §8).
- [ ] **Apply the `is_active=True` fix to `Party`'s unique constraints** for consistency with
      `Item`. Today a soft-deleted party's GSTIN/PAN still blocks re-adding that party, which is a
      real support annoyance ("I deleted them by accident and now cannot re-add them").
- [ ] **Confirm `requirements.txt` is regenerated from `pyproject.toml` on dependency changes.**
      Render installs from `requirements.txt`, not `pyproject.toml`, so the two can drift silently.
      Command: `uv export --no-dev --no-hashes --no-emit-project -o requirements.txt`.
- [ ] **Add a `GET /api/v1/meta/` index** listing the dropdown sources (`states`, `units`,
      `gst-rates`) so the frontend can discover them instead of hardcoding paths.
- [ ] **Timezone discipline for invoices.** `TIME_ZONE` is `Asia/Kolkata` with `USE_TZ=True`.
      Store `invoice_date`/`due_date` as `DateField` (no time component) — an invoice dated
      "7 Oct" must not shift to 6 Oct because of UTC conversion.

### 9c. Someday / low urgency

- [ ] **Cache `ensureOptions()` results per session.** Units and GST rates are static; the items
      page refetches them on every open. A module-level cache in JS (or one combined meta
      endpoint) removes two round trips per page load — noticeable on Render's cold starts.
- [ ] **Bulk import for items** (paste a CSV of a supplier's catalogue). Most real catalogues
      arrive as a spreadsheet, so this is the single most-requested item feature.
- [ ] **Item reorder / sort order field** for a user-defined catalogue order.
- [ ] **Track `last_purchase_price` vs `purchase_price`** as separate fields once purchases exist.
- [ ] **Item image / thumbnail** (Cloudinary), so the catalogue and invoice lines are recognisable.
- [ ] **Unit conversion** (buy in KG, sell in PCS) — a carton/box conversion table per item.
- [ ] **Group items into categories** for a tidier catalogue and grouped reports.
- [ ] **OpenAPI / Swagger** (`drf-spectacular`) so the Flutter app and any integration have a
      contract (already listed in §4).

---

## 10. Testing Gaps (found during the 1.3 review)

- [ ] **Every new endpoint must have at least one test that hits the literal public URL.**
      A double-prefixed router published `/api/v1/items/items/` and left `POST /api/v1/items/`
      returning 405, while all 61 existing tests passed — because they all used `reverse()`,
      which resolved the wrong URL happily. The frontend calls hardcoded paths, so `reverse()`
      tests prove nothing about what the browser actually requests.
- [ ] **Frontend render tests are ad-hoc and unversioned.** The list-rendering checks for parties
      and items live in a temp folder, not in the repo. Move them into
      `Web_Frontend/tests/` (Node + jsdom or a plain DOM stub) so a broken empty state or a
      dropped `h()` attribute cannot ship again.
- [ ] **End-to-end browser test** (Playwright) for the three critical flows: register → complete
      business profile → create party → create item → create invoice.
- [ ] **Rounding tests for the GST calculator** once Phase 1.4 lands: amounts that produce
      fractions of a paisa, and a check that the JS preview matches the Python total to the paisa.
      *(Partly covered by 1.4.2's golden + invariant tests; this entry stays for the extra
      fractional-paisa cases added later.)*

---

## 11. Phase 1.4 Review — invoicing engine (added after the 1.4 plan was locked)

Surfaced while designing the GST engine and **deliberately deferred**, so nothing here blocks 1.4.

### 11a. Invoice features deferred out of 1.4

- [ ] **Credit / debit notes.** Currently the only correction route after issue is cancel-and-reissue,
      which is wrong once the period's GSTR-1 is filed. Needs its own numbering series, and for
      `CANCELLED` invoices a reference to the original.
- [ ] **Reverse charge.** No field in 1.4 by design. When added it needs precise semantics: tax
      computed and shown but **excluded from the payable total**, plus the mandated declaration text.
      A `default=False` column is safe for every existing invoice.
- [ ] **Nil-rated vs exempt as separate rates.** `GST_RATE_CHOICES` currently has a single `0.00`
      covering both, but GSTR-1 reports them in **different tables**. Splitting it later is a
      migration plus a change to the calculator's registration gate.
- [ ] **Compensation cess** on luxury/demerit goods — no cess column exists, so no 1.4 invoice can
      carry one. Confirm no user needs it (see 1.4.8 compliance Q8).
- [ ] **Turnover-based HSN thresholds.** The `HSN_REQUIRED` gate is unconditional, which is stricter
      than the statutory ₹5,000-per-invoice B2B threshold. Make the threshold a business setting.
- [ ] **SEZ / OIDAR (state codes 96/97).** Explicitly rejected for now; they need a separate
      "zero-rated supply" concept rather than a rate of zero.
- [ ] **One POS per invoice only.** A single invoice mixing goods delivered to two states is
      currently impossible by design (decision 4). If real customers hit it, it becomes two invoices.
- [ ] **Save a free-text line as an inventory item** — the billing form's "+ Custom line" has no
      "also create this item" action.
- [ ] **Staff logins per business.** `BusinessProfile.user` is still `OneToOne`, so one person per
      business. 1.4 adds nullable `created_by` / `issued_by` / `cancelled_by` so the audit trail
      survives the later migration, but real staff access still needs a many-to-one membership model.
- [ ] **Invoice amendment trail** — who edited a draft, and when. Drafts are editable, so there is
      currently no history of a draft's changes.

### 11b. Invoicing infrastructure deferred

- [ ] **Per-business invoice series across FYs** — a run of numbers with no gap (e.g. a destroyed
      book), required for some audits. `InvoiceCounter` supports the FY reset but not series runs.
- [ ] **Invoice numbering configurable without an FY segment** for shops that do not use it.
- [ ] **Bulk invoice actions** — download a range as a PDF pack, or email a batch.
- [ ] **Duplicate-invoice detection** — warn (do not block) when an identical party + amount + date
      is issued twice, which is usually a double-click or a re-save.
- [ ] **Recurring / scheduled invoices** for rent, subscriptions and retainers.
- [ ] **Invoice hold / approval flow** — a second person approves before issue, for larger businesses.
- [ ] **`assertNumQueries` on the invoice list** once invoices exist in volume, plus verifying the
      `(business, invoice_date)` / `(business, status)` / `(business, party)` indexes are actually
      used by the filters rather than sitting unused.
- [ ] **Partition or archive issued invoices by financial year** if a single tenant's invoice count
      grows large enough that list queries slow down.

---
