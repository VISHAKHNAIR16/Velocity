# TASKS.md — Master Project Tracker

This document tracks the step-by-step implementation of the GST Billing, Inventory, and Accounting System. Update task checkboxes as features are completed to maintain a clear state of progress.

---

## Task Management Rules for AI & Developers
1. **Never skip a phase:** Complete database models, API views, validation logic, and frontend UI tests before marking a parent task as completed.
2. **Multi-Tenancy Check:** Ensure every newly created model includes a FK to `User` / `BusinessProfile` and every query is scoped through `TenantModelViewSet` / `for_business()` (by `business`, never by raw `user`).
3. **Financial Precision Check:** NEVER use `float` for money or stock.
   - **Currency / tax / prices** → `DecimalField(max_digits=12, decimal_places=2)`.
   - **Stock quantities** → `DecimalField(max_digits=12, decimal_places=3)`, because a 2-decimal field silently rounds a 1.5 kg jar to `1.50`, and stock then drifts permanently against the physical count after every deduction.
   - Any computed money value must stay `Decimal` end-to-end (ledger maths included).
4. **Testing Threshold:** Mark tasks completed only after verifying success responses via postman/cURL or UI form execution.

---

## Phase 0: System Architecture & Cloud Infrastructure **COMPLETED**

- [X] **0.1 Development Environment & Repository Setup**
  - [X] Initialize Git repository with proper `.gitignore` (excluding `.env`, virtual environment, static media).
  - [X] Set up Python virtual environment and `requirements.txt` (`django`, `djangorestframework`, `django-cors-headers`, `dj-database-url`, `psycopg2-binary`, `django-cloudinary-storage`, `python-dotenv`).
  - [X] Create base `.env` file and `.env.example` template.

- [X] **0.2 Cloud Database & Media Integration**
  - [X] Provision PostgreSQL instance on **Supabase** (or **Neon.tech**).
  - [X] Configure `settings.py` to use `dj-database-url` for secure database SSL connections.
  - [X] Set up **Cloudinary** account and integrate `django-cloudinary-storage` for public/private media storage (logos, PDF invoices).
  - [X] Test database migrations against cloud PostgreSQL instance (`python manage.py migrate`).

- [X] **0.3 Web Hosting Setup**
  - [X] Set up Web Service on **Render** (or **Koyeb**) connected to GitHub repository.
  - [X] Configure Render build command: `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate`.
  - [X] Set up static app hosting on **Vercel** (or Netlify) for the HTML/CSS/JS frontend application.
  - [X] Configure CORS policy in `settings.py` using `django-cors-headers` to permit cross-origin requests from Vercel domain.

---

## Phase 1: Core Foundation & GST Invoicing Engine — **IN PROGRESS** (1.1–1.4 built ✅ · review findings tracked in 1.4.9 and closed in 2.0)

### 1.1 Authentication & Business Setup
- [X] **1.1.1 User & Multi-Tenant Data Schema**
  - [X] Implement Django REST Framework JWT/Session Authentication (`rest_framework_simplejwt` or default token auth).
  - [X] Build `BusinessProfile` Model:
    - Business Name, Owner Name, Mobile, Email.
    - GSTIN (15-character alphanumeric validation).
    - Address Line, City, State Code (2-digit GST state code), Pincode.
    - Business Logo (ImageField configured with Cloudinary).
    - Bank Account Details (Account Name, Account Number, IFSC Code, Bank Name, Branch).
  - [X] Create `BusinessProfileSerializer` and API endpoints (`GET /api/v1/business/profile/`, `PUT /api/v1/business/profile/`).
  - [X] Build UI page for User Registration, Login, and Business Profile Setup.

- [X] **1.1.2 Multi-Tenancy Foundation (Core App)**
  - [X] Create `TenantModel` abstract base class with `business` FK, `created_at`, `updated_at`.
  - [X] Create `TenantQuerySet` with `for_business()` method for scoped queries.
  - [X] Create `TenantQuerysetMixin` and `TenantModelViewSet` for auto-scoped CRUD views.
  - [X] Register `apps.core` in `INSTALLED_APPS`.

- [X] **1.1.3 Business Profile Enhancements**
  - [X] `LogoUploadSerializer` with image format/size validation (PNG/JPG/WebP, 2MB).
  - [X] `StateListView` at `/api/v1/meta/states/` for frontend dropdowns.
  - [X] Cross-field GSTIN validation: first 2 digits match `state_code`, chars 3-12 match PAN.
  - [X] `is_complete` computed field for profile completion status.
  - [X] Auto-uppercase GSTIN, PAN, IFSC on input.

### 1.2 Parties Master (Customers & Vendors) — **COMPLETED ✅ VERIFIED**
- [X] **1.2.1 Data Schema & API**
  - [X] Build `Party` Model:
    - User/Business FK (Tenant context via `TenantModel`).
    - Name, Mobile, Email.
    - Party Type (`CUSTOMER`, `SUPPLIER`, `BOTH`).
    - GSTIN (Optional for unverified/unregistered parties).
    - PAN (Optional).
    - State Code (Crucial for determining CGST+SGST vs IGST).
    - Billing Address (Line, City, Pincode).
    - Shipping Address (Line, City, Pincode).
    - Opening Balance (`DecimalField`, max_digits=12, decimal_places=2) & Balance Type (`CREDIT`, `DEBIT`).
    - `is_active` flag for soft-delete.
  - [X] Create `PartySerializer` with regex validation for GSTIN, PAN, mobile, pincode.
  - [X] Create `PartyListSerializer` (lightweight for list views).
  - [X] Implement `PartyViewSet` extending `TenantModelViewSet`:
    - `GET /api/v1/parties/` (List with filters: `party_type`, `search`, `is_active`).
    - `POST /api/v1/parties/` (Create customer/vendor).
    - `GET /api/v1/parties/{id}/` (Retrieve).
    - `PUT/PATCH /api/v1/parties/{id}/` (Update).
    - `DELETE /api/v1/parties/{id}/` (Soft-delete: sets `is_active=False`).
  - [X] Add URL routing in `apps/parties/urls.py` and include in `config/urls.py`.
  - [X] Run migrations (`0001_initial`, `0002_alter_party_opening_balance` — Decimal validator, no schema change).

- [X] **1.2.2 Frontend: Parties Directory**
  - [X] Create `Web_Frontend/parties/index.html` — filter bar (type / search / status / sort), responsive table, sidebar drawer form, delete confirmation.
  - [X] Add "Parties" link to navigation bar (`ui.js`).
  - [X] Create `Web_Frontend/parties/parties.js` for:
    - [X] Fetching and rendering party list with pagination (page size synced to backend `PAGE_SIZE`).
    - [X] Add/Edit drawer with sections: Basic Details, Tax Details, Billing Address, Shipping Address, Opening Balance.
    - [X] Real-time GSTIN validation & state/PAN auto-fill.
    - [X] State dropdown from `/api/v1/meta/states/`.
    - [X] Form submission (create/update) with field-level validation feedback.
    - [X] Soft-delete confirmation + restore.
  - [X] Distinct **empty / loading / error** states (friendly notes, never error-styled for "no data").
  - [X] Active-filter chips, row hover, avatar initials, Indian-format currency (`₹1,50,000.00`).
  - [X] Spaced action buttons (edit / delete) with per-action hover colours to avoid misclicks.
  - [X] Style with Tailwind CSS consistent with existing pages.

- [X] **1.2.3 Testing & Verification** — automated suite: `apps/parties/tests.py`
  - [X] Automated CRUD tests (create / retrieve / update / soft-delete / restore).
  - [X] Automated validation tests (GSTIN format, GSTIN ↔ state, GSTIN ↔ PAN, mobile, pincode, duplicate GSTIN/PAN).
  - [X] Automated filter & search tests (`party_type`, `search`, `ordering`, `is_active`).
  - [X] Automated model tests (`opening_balance_signed` returns `Decimal`, address fallback, display state).
  - [X] **Multi-tenancy isolation tests (15 tests)** — list, count, search, autocomplete, filters, retrieve, update, delete, restore, `business` FK cannot be spoofed, duplicate-GSTIN scoping, DB-level unique constraint, anonymous rejection, superuser without profile.
  - [X] Backend: cURL tests for all CRUD endpoints + validation errors.
  - [X] Frontend: Create customer with GSTIN → state auto-fills.
  - [X] Frontend: Create supplier without GSTIN (unregistered) → works.
  - [X] Frontend: GSTIN-state mismatch → validation error shown.
  - [X] Frontend: Soft delete → party hidden from list, preserved for invoice FKs.
  - [X] Frontend: Filter by party_type (Customer/Supplier/Both) works.
  - [X] Frontend: Search by name/mobile/GSTIN works.

Run the suite with:
`uv run python manage.py test apps --settings=config.settings_test` — **53 tests, all passing.**

#### Phase 1.2 → 1.3 decisions (resolved)

| # | Question | Decision | Consequence |
|---|---|---|---|
| 1 | Missing `BusinessProfile.state_code` | **Block invoice creation only.** Keep the profile field optional. | No schema change. Add a guard in the **1.4** invoice service that rejects invoice creation with `error: "BUSINESS_PROFILE_INCOMPLETE"`. The existing dashboard/profile banner already nudges users. Tracked below as a 1.4 task. |
| 2 | Shared tax constants | **New `apps/core/constants.py`**; `accounts/constants.py` re-exports for backwards compatibility. | Single source of truth for GST rates, units and HSN/SAC so `Item`, `InvoiceItem` and `PurchaseItem` cannot drift. |
| 3 | `Item` soft delete | **Yes — mirror `Party`.** | One `is_active` field + conditional unique constraints. Prevents CASCADE destroying invoice lines, PROTECT blocking retirement, and SET_NULL orphaning history. |
| 4 | Service stock | **Nullable stock fields + `PRODUCT` gate.** | `null` stops misleading displays; the gate stops bad arithmetic. Without this, a service at `0` stock makes Phase 2.1 reject every invoice of that service. |
| 5 | Drawer layout | **Keep stacked sections** (no tabs). | Matches what is built; the whole record is visible at once so an opening balance can't be silently forgotten. **Deviation from the original "tabs" wording is accepted.** |
| 6 | Stock decimal places | **Stock `Decimal(12,3)`, money `Decimal(12,2)`.** | Rule 3 amended accordingly. Prevents stock drift against physical counts. |

### 1.3 Inventory Basics (Items & Services Master)

New Django app: **`apps/inventory`** (`models.py`, `serializers.py`, `views.py`, `urls.py`, `tests.py`, `admin.py`, `migrations/`), registered in `INSTALLED_APPS`.

---

#### 1.3.0 Shared Foundations (do these first)

- [X] **Extract shared DOM helpers into `shared/js/ui.js`**
  - `h()` — hyperscript **with attribute-object support**: `h("td", { className, textContent, style, dataset, ariaLabel, on* }, ...children)`, plus `isProps()` / `applyProps()`.
  - `debounce()`, `formatINR()`, `initials()`.
  - Delete the private copies from `parties/parties.js` and consume the shared globals.
  - **Why:** `h()` was duplicated per page, and the copy in `parties.js` originally lacked props support — silently producing empty `<td>`s and a blank list. Centralising makes that bug class impossible.
  - Regression: re-run the parties render test (20 checks) — green, now driven by the **real** `ui.js` helpers rather than stubs.

- [X] **Create `apps/core/constants.py`** (single source of truth for tax data)
  - `GST_STATE_CHOICES` — **moved** here from `apps/accounts/constants.py` (36 codes, verified identical).
  - `GST_RATE_CHOICES` — `0, 0.25, 1.5, 3, 5, 12, 18, 28` as **`Decimal`** values, plus `VALID_GST_RATES` and `DEFAULT_GST_RATE`.
  - `MEASURING_UNITS` — 21 units, longest code 5 chars (fits a `max_length=8` column).
  - `HSN_CODE_LENGTHS = (4, 6, 8)` and `SAC_CODE_LENGTHS = (4, 6)` with `HSN_PATTERN` / `SAC_PATTERN`.
    - **Correction to the original plan:** HSN and SAC ranges **overlap** — a 6-digit code is valid for both, so a code cannot be classified by its digits alone. The real rule is that HSN/SAC codes are only ever **4, 6 or 8** digits (5 and 7 are never valid), and SAC never exceeds 6.
  - Helpers: `state_name()`, `valid_gst_rate()`, `gst_rate_label()`, `is_hsn()`, `is_sac()`.
  - **Rewire imports:** `accounts/constants.py` is now a re-export so `accounts/models.py` + `accounts/views.py` are untouched; `parties/models.py` + `parties/serializers.py` use the canonical `apps.core.constants`.
  - **No circular import:** `core/constants.py` imports nothing from `accounts`.

---

#### 1.3.1 Item Data Schema

- [X] **Build `Item` model** (inherits `TenantModel` for the business FK + timestamps)
  - **Identity:** `name`, `item_code` (SKU, optional), `barcode` (optional).
  - **Classification:** `item_type` (`PRODUCT` / `SERVICE`, indexed).
  - **Tax codes:** `hsn_sac_code`; `service_description` (mandatory for services on a tax invoice).
  - **Unit:** `unit` from `MEASURING_UNITS`, default `PCS`.
  - **Pricing:** `sales_price` `Decimal(12,2)`; `purchase_price` `Decimal(12,2)` nullable; `price_includes_tax` boolean; `tax_rate` `Decimal(5,2)` from `GST_RATE_CHOICES`, default `18.00`.
  - **Stock:** `current_stock` and `low_stock_threshold`, both **`Decimal(12,3)` and nullable** — `NULL` means "not stock-tracked" (services).
  - **Status:** `is_active` boolean, indexed, soft-delete.
  - `__str__`, `get_display_unit()`, `tracks_stock` property.
  - `Meta`: `ordering = ["name"]`; indexes on `(business, item_type, is_active)`, `(business, name)`, `(business, item_code)`.
  - **Conditional unique constraints** (mirroring `Party`) so a soft-deleted item's code/barcode can be reused:
    - `(business, item_code)` where `item_code != ''`
    - `(business, barcode)` where `barcode != ''`

- [X] **Stock semantics helper** — so Phases 2.1 and 3.3 can't forget the gate
  - `Item.tracks_stock` → `item_type == PRODUCT`
  - `Item.objects.stock_tracked()` queryset filter (`item_type=PRODUCT`, `current_stock__isnull=False`)
  - `is_low_stock` property → `tracks_stock and current_stock <= low_stock_threshold`

- [X] **Run migrations** and confirm `makemigrations --check` reports no drift.

---

#### 1.3.2 Validation Rules

- [X] **`ItemSerializer`** cross-field validation:
  - `PRODUCT` → `hsn_sac_code` must match `HSN_PATTERN` (blank allowed for now, warned in the UI).
  - `SERVICE` → `hsn_sac_code` must match `SAC_PATTERN`, and `service_description` is required.
  - **Length rules are asymmetric, not "vice versa":** 5- and 7-digit codes are rejected for **both** types; an 8-digit code is valid for a product but rejected for a service. A 6-digit code is legitimately valid for either, so it cannot (and must not) be used to tell a product's HSN from a service's SAC.
  - Prices must be `>= 0`.
  - `tax_rate` must be a member of `GST_RATE_CHOICES`.
  - **Services force stock fields to `NULL`** (`current_stock`, `low_stock_threshold`) so a misleading `0` can never be stored.
  - Auto-uppercase `item_code`, `barcode`, `hsn_sac_code`.
  - Duplicate `item_code` / `barcode` within the same business → clear field error.

- [X] **Decimal guard:** assert prices come back as `Decimal` (never `float`) in tests.

---

#### 1.3.3 API

- [X] **`ItemViewSet(TenantModelViewSet)`** at `/api/v1/items/`
  - `GET /api/v1/items/` — list, paginated, filters: `item_type`, `search`, `is_active`, `low_stock`, `ordering`.
  - `search` across `name`, `item_code`, `barcode`, `hsn_sac_code`.
  - `POST /api/v1/items/` — create.
  - `GET /api/v1/items/{id}/` — retrieve.
  - `PUT/PATCH /api/v1/items/{id}/` — update.
  - `DELETE /api/v1/items/{id}/` — **soft delete** (`is_active=False`), never a hard delete.
  - `POST /api/v1/items/{id}/restore/` — restore a soft-deleted item.
  - `GET /api/v1/items/search/?q=&item_type=` — lightweight autocomplete for the **1.4** billing form.
  - `ordering_fields`: `name`, `item_type`, `sales_price`, `current_stock`, `created_at`.
  - Multi-tenancy inherited from `TenantModelViewSet` — verify with the isolation tests.

- [X] **`ItemListSerializer`** — lightweight row payload (name, type, code, unit, prices, tax rate, stock, is_active).

- [X] **URL routing** in `apps/inventory/urls.py`, included in `config/urls.py` under `/api/v1/`.

---

#### 1.3.4 Frontend: Items & Services Catalog

- [X] **`Web_Frontend/items/index.html`** — filter bar, table, drawer form, delete confirmation.
- [X] **`Web_Frontend/items/items.js`** — built on the shared `ui.js` helpers:
  - [X] Live search (debounced) + `item_type` / `is_active` filters + sorting.
  - [X] Pagination (page size synced to backend `PAGE_SIZE`).
  - [X] Add/Edit drawer with sections: Basic, Tax Codes, Pricing, Stock.
  - [X] **Product/Service switch** — selecting **Service** hides stock inputs and relabels the code field to **SAC Code** with the service-description field appearing; **Product** shows stock + threshold and labels it **HSN Code**.
  - [X] Stock columns omitted for services.
  - [X] Low-stock visual cue (only for stock-tracked products below threshold).
  - [X] Validation feedback per field, loading state, friendly empty state, real error state.
  - [X] Soft-delete confirmation + **Restore** for inactive items.
- [X] **Nav:** add an **Items** link in `shared/js/ui.js` (`renderNav`) and mark active on this page.
- [X] **Styling:** Tailwind utilities consistent with `theme.css`; reuse `.card`, `.input`, `.btn`, `.badge`, drawer and toast patterns.

---

#### 1.3.5 Testing & Verification — `apps/inventory/tests.py`

- [X] **Tenant isolation** (mirroring the parties suite)
  - [X] List, `count` and search never return another tenant's items.
  - [X] Filters (`item_type`, `is_active`, `low_stock`) don't leak.
  - [X] Retrieve / update (PATCH + PUT) / delete / restore of another tenant's item → **404**.
  - [X] `/items/search/` autocomplete is scoped.
  - [X] Created item is stamped with the caller's business; a payload-supplied `business` is ignored.
  - [X] Duplicate `item_code` is rejected **within** a business but allowed **across** businesses.
  - [X] Anonymous access → 401.
  - [X] Superuser without a profile gets one created (no `RelatedObjectDoesNotExist`).

- [X] **CRUD**
  - [X] Create / update; `item_code`, `barcode`, `hsn_sac_code` upper-cased.
  - [X] DELETE is a soft delete (row survives, `is_active=False`).
  - [X] Soft-deleted items leave the default list and return via `?is_active=false`.
  - [X] Restore brings the item back.
  - [X] Partial update leaves untouched fields alone.

- [X] **Validation**
  - [X] Product with a 5- or 7-digit code → rejected (never valid for goods or services).
  - [X] Service with an 8-digit code → rejected (SAC never exceeds 6 digits).
  - [X] Product with a valid 8-digit HSN → accepted.
  - [X] Service with a valid 6-digit SAC → accepted (6 digits are valid for both types).
  - [X] Service without `service_description` → rejected.
  - [X] Service with stock submitted → stored as `NULL`.
  - [X] Negative price → rejected.
  - [X] Invalid `tax_rate` → rejected.

- [X] **Model**
  - [X] `tracks_stock` true for products, false for services.
  - [X] `is_low_stock` correct at/below/above the threshold.
  - [X] Stock retains 3-decimal precision (`Decimal("1.500")` survives a round trip).
  - [X] Prices come back as `Decimal`, never `float`.

**Result:** 68 inventory tests, of which **17 are tenant-isolation tests** and **7 guard the literal
public URL paths**. Full suite:
`uv run python manage.py test apps --settings=config.settings_test` — **121 tests, all passing.**

---

#### 1.3.6 Housekeeping

- [X] Register `Item` in `apps/inventory/admin.py`.
- [X] Run `manage.py check` and `makemigrations --check` (no drift).
- [X] Full suite green; update this section with final test counts.
- [X] Added `/api/v1/meta/units/` and `/api/v1/meta/gst-rates/` so the frontend dropdowns are
  served from `apps.core.constants` instead of a second copy in JavaScript.

---

#### 1.3.7 Issues found while testing (and fixed)

- [X] **🐛 CRITICAL — double-prefixed router: `POST /api/v1/items/` returned 405.**
  When the meta endpoints were added, `apps/inventory/urls.py` changed from
  `path("", include(router.urls))` to `path("items/", include(router.urls))` — but the
  router is *already* registered with the `r"items"` prefix, so every route was published
  one segment too deep:

  | Request | Resolved to | Result |
  |---|---|---|
  | `GET /api/v1/items/` | router's read-only **API root** | 200 + 53 bytes of router metadata (looked like an empty list) |
  | `POST /api/v1/items/` | API root (GET-only) | **405 Method Not Allowed** |
  | `GET /api/v1/items/{id}/` | nothing | 404 |
  | `GET /api/v1/items/search/` | nothing | 404 |
  | (real endpoints) | `/api/v1/items/items/…` | double `items` segment |

  Fixed by restoring `path("", include(router.urls))` and keeping only the two `meta/`
  paths explicitly prefixed. `POST /api/v1/items/` now returns **201**; verified live for
  create / retrieve / patch / delete / restore / search / all filters.

- [X] **Why 121 tests missed it — and the regression tests that now prevent it.**
  Every test called `reverse("item-list")`, which happily produced the *wrong* URL
  (`/api/v1/items/items/`) and passed. The frontend, however, calls **hardcoded** paths.
  Added `ItemUrlRoutingTests` (7 tests) which deliberately avoid `reverse()` and hit the
  literal strings the JS uses, plus an assertion that `/api/v1/items/items/1/` must 404.
  **Verified these tests fail (6/7) when the bug is reintroduced** and pass when it is fixed.
  - **Lesson for every future app:** any test that uses `reverse()` proves nothing about the
    URL the browser actually calls. Assert the literal public path at least once per endpoint.

- [X] **Conditional unique constraints did not exclude soft-deleted rows.** The constraints were
  `condition=Q(item_code__gt="")`, which only skipped *blank* codes — so a soft-deleted item's code
  still blocked re-use, contradicting the documented behaviour. Fixed by adding `is_active=True`
  to the condition (migration `inventory.0002`). Regression test:
  `test_database_constraint_allows_reusing_a_soft_deleted_items_code`.
  - **⚠️ The same gap still exists on `Party`** (`unique_gstin_per_business`,
    `unique_pan_per_business` are also only conditional on non-blank). A soft-deleted party's GSTIN
    currently blocks re-adding that party. Left unchanged deliberately to keep this phase's diff
    reviewable — decide whether to apply the same `is_active=True` fix.
- [X] **Submitting a real stock quantity for a `SERVICE` is rejected with a clear 400**
  (`"Services do not track stock; leave it empty."`) rather than silently discarded, while an
  explicit `null`/blank is accepted and stored as `NULL`. The frontend already sends `null`.
- [X] **`GST_STATE_CHOICES` move caught a typo** — the 36 state codes were re-verified byte-identical
  against the original list after moving them to `apps/core/constants.py`.
- [X] **Rewiring imports dropped `get_business` from `parties/serializers.py`** (caught by the
  parties suite: 12 errors). Restored. Worth remembering: editing an import block by
  *replacement* can silently delete a neighbouring import — re-run the suite after any such edit.

---

### 1.4 Core GST Calculation & Invoicing Engine

The heart of the product. Unlike a CRUD feature, a wrong decision here produces a **wrong tax
return** that cannot be quietly fixed later — every affected invoice would have to be reissued.

> **Status: plan reviewed twice, all decisions locked (see 1.4.0 and 1.4.7).** ⚖️ marks compliance
> points reflecting my understanding of GST rules as of mid-2026 — **confirm each with your CA
> before launch.**

**Sub-section numbers match the build order. Finish and verify each step before starting the next:**

| Section | Step | Scope | Touches DB? | "Done" means |
|---|---|---|---|---|
| **1.4.1** | A | Pre-flight fixes (small, boring, must come first) | migrations only | existing 121 tests still green |
| **1.4.2** | B | `money.py`, `fiscal.py`, calculator (pure Python) | **no** | golden + invariant tests pass |
| **1.4.3** | C | Models, migrations, draft CRUD, `/preview/` | yes | **done** — 290 tests green, preview == saved totals |
| **1.4.4** | D | Issue / cancel / numbering / concurrency | yes | **done** — concurrency tests green on Neon PostgreSQL (6/6); 357 tests pass on **both** SQLite and PostgreSQL |
| **1.4.5** | E | Frontend: invoice list + billing form | — | **done over HTTP** — 380 backend tests + 169 contract checks green; real-browser pass pending (2.0.9) |

---

#### 1.4.0 Locked decisions (do not re-litigate mid-build)

| # | Decision | Rule | Why |
|---|---|---|---|
| 1 | **Rounding** | Per line: taxable → 2dp `ROUND_HALF_UP`; tax per line → 2dp; invoice total = **exact sum of line totals** | Rounding only the grand total diverges by a paisa; printed lines would not add up. |
| 2 | **Intra-state split — split the AMOUNT, not the rate** | `tax = q2(taxable × rate/100)`; `cgst = tax/2` rounded **down** to 2dp; `sgst = tax − cgst` | Splitting the rate then multiplying twice can disagree with `tax` by a paisa (₹10.55 @ 5%: tax 0.53, but 2.5% each → 0.26 + 0.26 = 0.52). Displayed rate is `rate/2` each (0.25% → 0.125%). |
| 3 | **Discounts** | Line-level **and** invoice-level; invoice-level apportioned pro-rata (largest-remainder, ties broken by line order); tax on the **discounted** value; reject discount > value. | Shares sum exactly; deterministic. *Scope-cut option: if time is short, ship line discounts only.* |
| 4 | **Place of supply** | `Party.shipping_state_code` added (nullable). Default: **if any line is goods → shipping state, falling back to billing state; otherwise billing state.** Stored on the invoice and **always overridable**. Codes 96/97 rejected for now. | ⚖️ Section 10(1)(a): goods → place of delivery. Services → recipient's location. **Caveat (s.10(1)(b)):** if the buyer directs delivery to a *different person*, place of supply is the buyer's state, not the ship-to state — hence the override plus a hint under the field. One invoice = one place of supply; mixed cases mean two invoices. |
| 5 | **Tax-inclusive prices** | **Invoice-level** flag `prices_include_tax`. `taxable = q2(net/(1+rate/100))`, **`tax = net − taxable`** (never recomputed). `Item.price_includes_tax` only pre-fills the toggle. | Recomputing tax from taxable breaks the total: ₹100 @ 18% → 84.75 + 15.26 = **₹100.01**. Extracting gives 15.25 → ₹100.00. |
| 6 | **Lifecycle** | `DRAFT` → `ISSUED` (frozen) → `CANCELLED` (reason + audit). Credit notes deferred. **"Copy as new draft"** makes cancel-and-reissue painless. | ⚖️ After GSTR-1 for that period is filed, a mistake needs a credit note — the cancel UI must warn about this. |
| 7 | **Round off** | Optional per business, default OFF (see 1.4.2). | Business's choice. |
| 8 | **Registration type** | `BusinessProfile.gst_registration_type`: `REGULAR` / `COMPOSITION` / `UNREGISTERED`. Not `REGULAR` → **tax forced to 0**, title `Bill of Supply`. | ⚖️ Unregistered/composition businesses may not charge GST on invoices. |
| 9 | **Walk-in customer** | Auto-create one `Walk-in / Cash Customer` party per business (state = business state, unregistered, no shipping state). Quick-add party inside the billing form. | `Invoice.party` is a required PROTECT FK; retail sales are the majority case. |
| 10 | **Invoice number** | `<PREFIX>/<YY-YY>/<NNNNN>` e.g. `INV/26-27/00001`. Prefix 1–4 chars `A-Z 0-9 -`. Whole number **≤ 16 chars**. | ⚖️ Rule 46: max 16 chars; letters, digits, `-`, `/` only. |
| 11 | **Issue lock order** | Lock **invoice row first, then counter row**; re-check `status == DRAFT` after the lock. | Locking only the counter lets two concurrent issues both pass the status check and burn a number. |
| 12 | **Rates** | Add **40** to `GST_RATE_CHOICES`; keep 12 and 28 (historical invoices). | ⚖️ GST restructured 22 Sept 2025: mainly 5% / 18%, with 40% for specified goods. |
| 13 | 🆕 **Goods vs service is a line snapshot** | `InvoiceItem.item_type` is copied when the line is saved and **never re-read from the live item**. Place-of-supply and stock rules read the snapshot. | Changing an item's type later must not silently reinterpret old invoices; free-text lines have no live item at all. |
| 14 | 🆕 **Reverse charge deferred — no field in 1.4** | No `reverse_charge` column now. The 2.3 PDF prints the constant line "Tax payable on reverse charge: No". | Outward reverse-charge supplies are rare for item-billing shops; semantics (tax computed and reported but excluded from the payable total) are easy to get wrong. A later column with `default=False` is correct for every existing invoice. |
| 15 | 🆕 **Free-text lines allowed** | `InvoiceItem.item` is a **nullable** FK (`PROTECT`). A line with no item must carry its own `item_name`, `item_type`, `unit`, `hsn_sac_code`, `tax_rate` (+ `service_description` for services). Enforced by a DB check constraint and the serializer. They never touch stock. | Shops bill transport/misc charges constantly; dummy inventory items pollute stock and low-stock alerts. |
| 16 | 🆕 **Audit columns** | Nullable FKs to `User` on `Invoice`: `created_by`, `issued_by`, `cancelled_by` (`on_delete=SET_NULL`, `related_name="+"`), set in the service layer. | ~3 columns now vs. a backfill migration when staff logins arrive. `BusinessProfile.user` stays OneToOne for now. |
| 17 | 🆕 **Backdating** | *(Amended by decision 23: lock date.)* Allowed. FY derives from `invoice_date`. UI warns when earlier than the latest issued invoice's date. | Shops enter yesterday's bills; stricter locking can be a later setting. |

---

#### 1.4.1 Step A — Pre-flight fixes (before any invoice code)

- [X] **`GST_RATE_CHOICES`:** add `40`; update `gst_billing_app_specification.md` (rate list is stale; also change its "auto-calculate in JS" wording — the server `/preview/` is the only tax-maths source). Add a **comment on the constant**: `0` currently covers both *nil-rated* and *exempt*, which GSTR-1 reports in separate tables — **do not treat them as interchangeable**; splitting them is a later migration.
- [X] **`TIME_ZONE = "Asia/Kolkata"`, `USE_TZ = True`**; use `timezone.localdate()` wherever dates are defaulted or the FY is derived (with UTC, an invoice made 00:00–05:30 IST gets yesterday's date and can land in the wrong FY on 1 April).
- [X] **`Party` soft-delete unique constraints:** add `is_active=True` to `unique_gstin_per_business` and `unique_pan_per_business` (as done for `Item`). `restore` returns a clear 400 if an active party now holds that GSTIN/PAN. Migration + regression tests.
- [X] **`Party.shipping_state_code`** (nullable, `CharField(2)`, choices from `GST_STATE_CHOICES`):
  - Serializer: validate against `GST_STATE_CHOICES`; reject a shipping state when no shipping address is entered.
  - Party drawer: a **"Ship to a different state"** toggle in the Shipping section (off = same as billing).
  - Tests: valid, invalid code, orphan shipping state, tenant isolation unaffected.
- [X] **`PARTY_STATE_MISSING`**: `Party.state_code` is already required, so keep this as a **backend guard only** — no UI.
- [X] **`BusinessProfile` migration** (+ serializer fields; UI comes in 1.4.5):
  - `gst_registration_type` — backfill: has GSTIN → `REGULAR`, else `UNREGISTERED`.
  - `round_invoice_total` `BooleanField(default=False)`.
  - `invoice_number_prefix` `CharField(default="INV", max_length=4)` + validator `^[A-Z0-9-]{1,4}$`.
  - **No** `invoice_number_next` — `InvoiceCounter` is the only counter. (Optional "starting number" for migrating shops seeds `InvoiceCounter.last_number`, editable only before the first issue of that FY.)
- [X] **Walk-in customer** created for every existing business (data migration) and for new businesses (on profile creation).
  - Only three `Party` fields are required: `name`, `mobile`, `state_code`. Use name `Walk-in / Cash Customer`, `state_code` = business state, `gstin=""`, `pan=""`, `shipping_state_code=None`, `party_type=CUSTOMER`.
  - Synthetic mobile must satisfy `^[6-9][0-9]{9}$` — use a fixed `9876543210`. `Party` has **no unique constraint on `mobile`** (only on `gstin`/`pan`), so one shared value is safe.
  - Look it up by `(business, name)` with `get_or_create` so it is idempotent.
- [X] **Add DRF throttling to `REST_FRAMEWORK` settings — currently there is none at all.** `/invoices/preview/` runs the full tax calculation on every keystroke-debounce, so it is the first endpoint worth protecting. Add `DEFAULT_THROTTLE_CLASSES` (`AnonRateThrottle`, `UserRateThrottle`, `ScopedRateThrottle`) and `DEFAULT_THROTTLE_RATES` (e.g. `user: 300/hour`, `preview: 120/minute`); apply `ScopedRateThrottle` with `throttle_scope="preview"` on the preview action only.
- [X] **PostgreSQL test database for the 1.4.4 concurrency tests.** `config.settings_test` uses in-memory SQLite, where Django's compiler (`django/db/models/sql/compiler.py:839`) only emits `FOR UPDATE` when `features.has_select_for_update` is True — SQLite inherits `False` from `base/features.py:49` and **silently ignores** `select_for_update()`. A concurrency test on SQLite passes whether or not the locking code exists, which is worse than no test.
  - **Decision: run concurrency tests against a local PostgreSQL in Docker, never against Neon.** Running `CREATE DATABASE` / `DROP DATABASE` against the same Neon instance that holds dev data is a footgun (we already hit a `test_neondb` teardown failure).
  - **⚠️ PREREQUISITE (not yet present on this machine):** Docker, the Compose plugin, and WSL2 are **all not installed**. Either install them (`wsl --install`, then Docker Desktop), or use a **separate Neon dev branch** as a zero-install fallback — Neon branching is already on the roadmap in `FUTURE_CHECKLIST.md` §2 and gives a real, pooled Postgres that production never touches.
  - Add `config/settings_test_pg.py` overriding `DATABASES` to that Postgres with a **distinct `TEST: {"NAME": ...}`**, so it can never collide with the fast SQLite suite.
  - Scope it: keep the main suite on SQLite for speed (121 tests in ~1s) and run only `InvoiceConcurrencyTests` against Postgres:
    ```
    uv run python manage.py test apps.invoices.tests.InvoiceConcurrencyTests --settings=config.settings_test_pg
    ```
  - The Postgres settings must **not** read the production `DATABASE_URL`; point it at the local container.
- [X] **Done when:** `makemigrations --check` clean and the full existing suite is green.

**Step A result — COMPLETE.** 145 tests green (accounts 24, parties 53, inventory 68); ruff clean;
no migration drift. Six migrations added and applied:
`parties.0003` (shipping_state_code + relaxed constraints),
`accounts.0004/0005/0006` (invoice preferences, backfill, walk-in parties),
`inventory.0003/0004` (40% rate, item image).
Verified on real data: 3 GSTIN businesses backfilled to `REGULAR`, 5 without to `UNREGISTERED`;
walk-in parties created only for the 3 businesses that have a state code (the rest are created
lazily by `get_or_create_walk_in_party()` on first use).

**Added in Step A beyond the original plan:** `Item.image` — an optional product photo as an
`ImageField` (mirroring `BusinessProfile.logo`), read-only on the serializer with its own
`PUT/DELETE /api/v1/items/{id}/image/` endpoint and `ItemImageUploadSerializer` (JPG/PNG/WebP,
2 MB). Chosen over a plain URL field so it gets Cloudinary, validation and a future thumbnail for
**2.3** invoice PDFs for free. Deleting the file is not tied to `is_active`, so an invoice reprint
can still render the photo of a soft-deleted item. *The upload UI is Step E; PDF rendering is 2.3.*

**Still open before Step D:** create the Neon `dev-test` branch and put its pooled URL in
`Backend/.env` as `TEST_DATABASE_URL` (`Backend/.env.example` documents it). Until then
`config/settings_test_pg.py` raises on purpose, so the concurrency gate cannot quietly pass on
SQLite.

---

#### 1.4.2 Step B — Money, fiscal helpers & GST calculator (pure Python, no DB)

- [X] **`apps/core/money.py`** — single owner of money rules (API, preview and PDF cannot disagree):
  - `q2()` / `q3()` — `ROUND_HALF_UP`.
  - `split_amount_intra(tax)` → `(cgst, sgst)`; always re-adds to `tax`.
  - `apportion_pro_rata(total, weights)` → parts summing **exactly** to `total` (largest-remainder, deterministic ties, handles all-zero weights).
  - `amount_in_words(amount)` → `"Rupees One Lakh Twenty Three Thousand Four Hundred Fifty Six and Seventy Paise Only"` (lakh/crore grouping; zero and paise-only cases).
- [X] **`apps/core/fiscal.py`** — `financial_year(date) -> "2026-27"`, `financial_year_short(date) -> "26-27"`, `fy_bounds(label)`. FY = 1 April–31 March.
- [X] **`apps/invoices/services/gst_calculator.py`:**
  - **Module docstring stating the invariant and its reason.** It is the guard against a future "optimisation" that moves tax maths into the browser:
    > The browser never computes tax. `POST /invoices/preview/` and invoice persistence both call `calculate_invoice()`. Do not add client-side tax maths: JavaScript `toFixed(2)` and `Decimal`/`ROUND_HALF_UP` disagree on exactly the half-paisa values GST billing hits — `2.675` → `2.67` vs `2.68`, `1.005` → `1.00` vs `1.01`, and `0.1 + 0.2 === 0.3` is `false`. The preview would silently disagree with the saved invoice by a fraction of a paisa, with no error shown.
  - `calculate_invoice(lines, business, place_of_supply, invoice_discount, prices_include_tax, round_invoice_total)` → totals + per-line dicts.
  - **Single entry point:** expose one `recalculate_invoice()` used by *both* `/preview/` and create/issue — not two code paths that happen to match today.
  - **Order per line:**
    1. `gross = q2(quantity × unit_price)`
    2. less `line_discount` (reject if > gross)
    3. less apportioned `invoice_discount_share`
    4. exclusive: `taxable = net`, `tax = q2(taxable × rate/100)`
       inclusive: `taxable = q2(net / (1 + rate/100))`, `tax = net − taxable`
    5. intra → `split_amount_intra(tax)`; inter → `igst = tax`
    6. `total = taxable + tax`
  - **Registration gate:** non-`REGULAR` business → every `tax_rate = 0`, tax = 0.
  - **Supply type:** `business.state_code` vs `place_of_supply`. ⚖️ Intra-supply within a union territory without a legislature is **CGST + UTGST**. The five such state codes are **`{"04", "26", "31", "35", "38"}`** (Chandigarh, Dadra & Nagar Haveli and Daman & Diu, Lakshadweep, Andaman & Nicobar, Ladakh) — put them in `apps/core/constants.py` as `UT_WITHOUT_LEGISLATURE`, not inline. Store the value in the `sgst` columns and expose `state_tax_label` → `SGST` / `UTGST`. **`state_tax_label` is derived from the snapshotted `place_of_supply`, never stored** (so there is no second source of truth).
  - **Default place of supply helper** (`default_place_of_supply(party, lines)`): any line with `item_type == PRODUCT` → `party.shipping_state_code or party.state_code`; else `party.state_code`.
  - Reject (never coerce): zero/negative quantity, negative price, empty lines, rate outside `GST_RATE_CHOICES`, invoice discount > total, blank business state or place of supply.
- [X] **Round off** (optional per business, default OFF):
  - OFF: `round_off = 0.00`; grand total = exact sum of line totals.
  - ON: `grand_total` = nearest rupee (HALF_UP) of the sum; `round_off = grand_total − sum` (±0.50).
  - Presentational only — taxable values and tax never change. `grand_total` (with round-off) is the "invoice value" in GSTR-1.
  - **Warning shown next to the UI toggle, word for word:**

    > Rounding changes only the amount payable on this invoice. It does **not** change any taxable
    > value or tax amount. When you file GSTR-1 or GSTR-3B, report the **taxable value and tax** shown
    > against each HSN/SAC code — never include the round-off as taxable value, and never treat it as a
    > discount. Your HSN-wise summary must stay on the pre-rounding figures. If your accounts are
    > audited, consider leaving rounding off so every invoice total equals the sum of its lines exactly.

  - `Round Off` row shown only when non-zero.
- [X] **Tests (no DB, no HTTP):**
  - All slabs (0, 0.25, 1.5, 3, 5, 12, 18, 28, 40) × intra/inter × inclusive/exclusive × line/invoice discounts; registration gate; UTGST label; `default_place_of_supply` (goods/service/mixed, with and without shipping state).
  - **`money.py` in isolation:** `q2`/`q3` rounding direction; `split_amount_intra` on every slab including the odd ones (`0.25%` → `0.12 + 0.13`, `1.50%` → `0.75 + 0.75`); `apportion_pro_rata` exactness + deterministic ties + all-zero weights + single-line; `amount_in_words` for zero, sub-rupee/paise-only, exact rupee, thousands, lakh, crore, and a `1,00,00,001` style edge.
  - **`fiscal.py` in isolation:** FY boundaries on **31 Mar / 1 Apr** (the whole point of the module), leap-year 29 Feb, `financial_year_short`, `fy_bounds` round-trip, and a FY derived from an IST-local date at 00:30.
  - **Golden tests** — expected numbers computed **independently** (spreadsheet/by hand), never copied from the code's own output.
  - **Invariants over randomised lines** (fixed, printed seed so failures reproduce):
    - `Σ line.total == grand_total` (round-off off)
    - `cgst + sgst + igst == Σ line.tax`, and per line `cgst + sgst == tax`
    - `Σ discount shares == invoice discount`
    - inclusive: `line.total == net` exactly (the ₹100 → ₹100.00 case)
  - Round-off on/off: taxable values identical either way.

**Step B result — COMPLETE.** 94 new tests (money 26 · fiscal 15 · calculator 37 · invariants 16);
full suite **239 tests green**; ruff clean; no migration drift. Every Step B test is a
`SimpleTestCase`, which **raises if the database is touched** — so "no DB in Step B" is enforced by
the framework, not merely claimed, and `money.py` / `fiscal.py` / `gst_calculator.py` import no
model or ORM symbol.

**Two real bugs were found and fixed while writing Step B** (both caught by the randomised
invariants, then re-verified by reintroducing each bug and watching the suite fail):

- [X] **Tax-inclusive division by 100 twice.** `taxable = net / (100 + rate) / 100` gave
      `taxable = 0.01` and `tax = 117.99` on a ₹118.00 inclusive line at 18%. The invoice *total*
      still balanced (because the tax is derived as `net − taxable`), which is exactly why it was
      dangerous — the printed taxable value and tax were nonsense. Fixed to `net × 100 / (100 + rate)`.
- [X] **Registration gate result discarded.** `_apply_registration_gate()` returns a rewritten line
      list but its return value was ignored, so a `COMPOSITION` / `UNREGISTERED` business still
      charged 18% — under a "Bill of Supply" title, the worst possible combination and precisely
      what decision 8 exists to prevent.
- [X] **Apportionment bound clarified.** An invoice-level discount may not exceed the
      *post-line-discount* amount (not the gross subtotal); this is now checked up front with a
      clear `INVALID_DISCOUNT` message.

---

#### 1.4.3 Step C — Schema, draft CRUD and `/preview/`

- [x] **New app `apps/invoices/`** (`models.py`, `serializers.py`, `views.py`, `urls.py`, `tests.py`, `admin.py`, `services/`), registered in `INSTALLED_APPS`. Use `TenantPrimaryKeyRelatedField` for `party` and each line's `item`.
- [x] **`Invoice`** (inherits `TenantModel`)
  - `party` FK → `PROTECT`.
  - `invoice_number` — allocated on **issue**; `UniqueConstraint(business, invoice_number)` conditional on non-blank.
  - `invoice_date`, `due_date` — `DateField`.
  - `place_of_supply` `CharField(2)`, `supply_type` (`INTRA`/`INTER`), `prices_include_tax`, `notes`, `terms`.
  - `status`, `issued_at`, `cancelled_at`, `cancellation_reason`; `created_by`, `issued_by`, `cancelled_by` (decision 16).
  - **Snapshots:** `recipient_name`, `recipient_gstin`, `recipient_state_code`, `recipient_address`, `shipping_address`, `business_name`, `business_address`, `business_gstin`, `business_state_code`, `document_title` (`Tax Invoice` / `Bill of Supply`).
  - Totals: `subtotal`, `total_discount`, `taxable_total`, `cgst_total`, `sgst_total`, `igst_total`, `round_off`, `grand_total`.
  - Indexes: `(business, invoice_date)`, `(business, status)`, `(business, party)`.
  - **Not in 1.4:** `reverse_charge` (decision 14), `paid_amount` / `balance_due` / `payment_status` (3.1).
- [x] **`InvoiceItem`** (inherits `TenantModel`; denormalised `business` FK)
  - `invoice` FK `CASCADE`; `item` FK → `PROTECT`, **nullable** (decision 15).
  - Snapshots: `item_name`, `item_type`, `hsn_sac_code`, `unit`, `service_description`.
  - Money: `quantity` `Decimal(12,3)`, `unit_price` `Decimal(12,2)`, `line_discount`, `invoice_discount_share`, `taxable_value`, `tax_rate` `Decimal(5,2)`, `cgst_amount`, `sgst_amount`, `igst_amount`, `total_amount`.
  - **DB check constraint:** a line is either item-backed or self-describing:
    `item IS NOT NULL OR (item_name != '' AND item_type != '' AND hsn_sac_code != '' AND unit != '')`.
    The serializer is the friendly first line of defence; the constraint is the backstop.
  - **`line.business` must be set from `invoice.business`, never from `get_business(request.user)`.** Both are normally the same tenant, but setting it from the parent makes divergence structurally impossible. Assert it in a test.
  - Line inputs are **frozen when the line is saved**; issuing recalculates from stored inputs, never from today's item-master price.
- [x] **`InvoiceCounter`** — `business`, `financial_year`, `last_number`, `number_prefix`, `UniqueConstraint(business, financial_year)`.
  - **Snapshot the prefix onto the counter row when the FY counter is created.** Otherwise a business that edits its prefix mid-FY gets a series like `INV/26-27/00001 … ABC/26-27/00008`, which looks like tampering. Already-issued numbers never change, but the rest of the FY should keep the original prefix.
- [x] **`InvoiceAdmin`** read-only for any non-`DRAFT` invoice (admin edits bypass service-layer protection).
- [x] **Serializers:** `InvoiceSerializer` (nested lines) + `InvoiceListSerializer`.
  - **Every computed money field is `read_only`:** `subtotal`, `total_discount`, `taxable_total`, `cgst_total`, `sgst_total`, `igst_total`, `round_off`, `grand_total`, and every per-line `taxable_value` / `*_amount` / `total_amount`. The request body carries **inputs only**; the server always recomputes. Otherwise a buggy or tampered client can post its own totals and the invoice records whatever the browser said.
  - Line with `item`: copy name/type/HSN/unit/description from the item at save time (user may override rate/price/discount).
  - Line without `item` (free-text): require name, type, unit, HSN/SAC (validated with `is_hsn`/`is_sac` rules as for `Item`), `tax_rate` ∈ `GST_RATE_CHOICES`, and `service_description` for services.
  - Test that `item.business == invoice.business`.
- [x] **API** `InvoiceViewSet(TenantModelViewSet)` at `/api/v1/invoices/` — filters `status`, `party`, `date_from`, `date_to`, `search` (invoice number + party name):
  - `POST /invoices/` → `DRAFT` (sets `created_by`); `GET/PATCH/DELETE /invoices/{id}/` (`PATCH`/`DELETE` only while `DRAFT`).
  - `POST /invoices/preview/` — recalculates **without saving**; DRF-throttled. **The only place GST maths runs for the UI.** Calls the same `recalculate_invoice()` as create/issue, so preview and saved invoice cannot diverge.
  - `POST /invoices/{id}/copy/` — new draft from any invoice.
  - HSN/SAC-wise tax summary computed on demand from line snapshots.
  - Draft displays as **"Draft"** + created date, never `Draft #<pk>` (a global pk leaks other tenants' volume).
  - Create wrapped in `@transaction.atomic`.
- [x] **Tests:** draft CRUD; free-text line rules (each missing field → 400; constraint at DB level); snapshot `item_type` unaffected by later item edits; tenant isolation (list/count/search/filters; retrieve/update/delete/copy of another tenant's invoice → 404; cross-tenant `party`/`item` → 400); **literal-URL tests** with hardcoded `/api/v1/invoices/...` strings, never `reverse()` (see 1.3.7).
- [x] **Preview-drift tests (the guard for the `/preview/` risk).** For the same inputs, `/invoices/preview/` and the persisted invoice must agree **exactly**, field by field, across a matrix of: all 9 GST slabs × intra/inter × inclusive/exclusive × line/invoice discounts × round-off on/off. Include the half-paisa boundary cases where JavaScript and `Decimal` disagree (`2.675`, `1.005`, `0.145`).
  - Assert that a request body **containing** client-supplied totals has them **ignored** — every money field is `read_only`, so the saved values must equal the server's recomputation regardless of what the client posted.
  - This is the test that fails loudly if anyone ever reintroduces client-side tax maths.

---

#### 1.4.4 Step D — Issue, cancel, numbering and integrity

- [x] **`POST /invoices/{id}/issue/`**, inside **one** `@transaction.atomic`:
  1. `select_for_update()` the **invoice row** first.
  2. Already `ISSUED` → return **200 with the existing invoice** (idempotent, no number burned). `CANCELLED` → 409.
  3. Re-validate and recalculate from stored line inputs.
  4. Issue-time gates (400 with machine-readable `error`): `BUSINESS_PROFILE_INCOMPLETE` (blank business `state_code`), `PARTY_STATE_MISSING`, and ⚖️ **`HSN_REQUIRED`** — a `REGULAR` business issuing to a recipient with a GSTIN needs an HSN/SAC on every line (minimum 4 digits; free-text lines included).
      - ⚖️ ~~Deliberately stricter than the statute.~~ **Resolved as decision 18:** the strictness is now `BusinessProfile.hsn_requirement` (`STRICT` default, `STATUTORY` opt-in). See the decision log at 1.4.7. Still on the 1.4.8 CA list.
  5. `InvoiceCounter.objects.get_or_create(...)` then `select_for_update()` the counter row (a bare locked `get()` fails on a new FY; `get_or_create` + the unique constraint resolves the first-invoice race).
  6. Increment, build the number (≤16 chars), snapshot party and business, set `issued_at`/`issued_by`, save.
  - **Lock order is always invoice → counter**, with a comment at the call site saying why: locking only the counter lets two concurrent issues both pass the `status == DRAFT` check and both allocate a number; the invoice lock serialises them. Reverse the order and that bug returns.
  - **Catch `IntegrityError` from `UniqueConstraint(business, invoice_number)` and return a clean 409** (`"That invoice number was just allocated to another session — please reload."`). Belt *and* braces: even with the row lock, a lost race should never surface as an unhandled 500.
  - **`select_for_update()` is a silent no-op on SQLite** (see 1.4.1) — never let a test pass here without the PostgreSQL settings, or the lock's absence goes unnoticed.
- [x] **`POST /invoices/{id}/cancel/`** — requires a reason; stamps `cancelled_at`/`cancelled_by`; **never frees the number**. UI warns: if that period's GSTR-1 is filed, issue a credit note instead.
- [x] Edit/delete of an `ISSUED` invoice → `400`, never a silent success.
- [x] Structure `issue` so 2.1's stock deduction slots **inside the same transaction** (skipping free-text lines and lines whose snapshot `item_type` is `SERVICE`).
- [x] **Tests:** draft editable / issued frozen; cancel needs reason; number never reused; issue-twice returns the same number; FY rollover on 1 April (IST); per-business independence; number ≤16 chars; prefix validation; each issue gate; tenant isolation for issue/cancel; literal-URL tests.
- [x] **`InvoiceConcurrencyTests` — must run on PostgreSQL** (`--settings=config.settings_test_pg`, per 1.4.1), using `TransactionTestCase` + threads:
  - same invoice issued twice → one number allocated
  - two different invoices issued concurrently → consecutive numbers, no gap, no duplicate
  - first-invoice-of-a-new-FY race (both threads hit a counter that does not exist yet)
  - a forced `IntegrityError` → clean **409**, never a 500
  - **Skip the class with a clear message when the configured backend lacks `select_for_update`**, so nobody mistakes a skipped test for a passing one.

---

#### 1.4.5 Step E — Frontend: invoice list and billing form

- [x] **Profile UI:** "Invoice preferences" section (registration type, prefix, round-off toggle with the verbatim warning).
- [x] `Web_Frontend/invoices/index.html` — list, same visual language as parties/items. Palette: Slate Navy `#0F172A`, Electric Blue `#2563EB`, Velocity Cyan `#06B6D4`, Sky Glow `#38BDF8`, Muted Slate `#64748B`.
- [x] `Web_Frontend/invoices/billing.html`:
  - [x] Party picker (search; shows address, GSTIN, billing + shipping state; Walk-in preselectable; quick-add party).
  - [x] **Place of supply** field defaulting per decision 4, editable, with the hint: *"Delivering to a different person on the buyer's instruction? Use the buyer's state."* Plus a "prices include tax" toggle.
  - [x] Dynamic line rows: item search **or "+ Custom line"** (free text with name, type, unit, HSN/SAC, tax rate), qty, rate, discount, live total.
  - [x] Invoice-level discount with per-line preview.
  - [x] Live totals from `/preview/` only — **never GST maths in JavaScript**. Debounce (~300 ms) and use `AbortController`/a request counter so a slow older response can't overwrite a newer one.
  - [x] HSN/SAC summary; Round-off row only when enabled (+ banner while on).
  - [x] **Pre-submit warnings:** business state missing, backdated invoice, HSN missing.
  - [x] Save draft → Issue (button disabled on click) → Print; Cancel with the credit-note warning; Copy as new draft.
  - [x] Add "Invoices" to `renderNav`; use shared `h()` helpers.
- [x] **Done when:** browser smoke test — walk-in sale (CGST+SGST), inter-state B2B sale (IGST), free-text transport line, inclusive-price invoice, draft → issue → cancel → copy.
  - **Verified** against a live `runserver` over real HTTP by two contract walks (`smoke_contract.py`, 144 checks; `smoke_step_e.py`, 25 checks), asserting every field the JS reads is actually present on the wire. All pass.
  - The walk-in case is what caught the decision-9 bug below, so it earned its keep.
  - Print is a `window.print()` + print stylesheet (chrome dropped, tint removed, no link URLs). **A real PDF with letterhead, bank details and a signature is still 2.3** — do not treat this as the PDF deliverable.

##### Decision 20 — Decision 9 was only half implemented: walk-in POS now falls back to the business state

> **Narrowed by decision 21 (2.0.2):** the fallback applies only to `Party.is_walk_in`; `PARTY_STATE_MISSING` is reachable again.

- [x] **Found during the step E acceptance walk.** A brand-new account could not create a walk-in sale at all.
- **The gap.** Decision 9 says "walk-in party state → business state", but `resolve_place_of_supply()` returned `""` whenever the party had no `state_code`, and the auto-created *"Walk-in / Cash Customer"* party always has a blank `state_code` (it is created before the business state is known, by migration `accounts.0006`). So `/preview/` answered `400 "Select a place of supply"` — the single most common retail invoice was impossible.
- **Resolution.** `resolve_place_of_supply(party, lines, business)` now falls back to `business.state_code` when the party has none, and all three call sites pass the business. Supplying a customer standing at your counter from your own state is the correct place of supply, and the field stays manually overridable (an explicit `place_of_supply` still wins, and is still preserved across recalcs).
- **Why not "identify walk-ins by name":** `Party` has no `is_walk_in` column, so the schema cannot separate a walk-in from a named customer who left their state blank. The business-state fallback is the only rule the data supports — and it is the one decision 9 asks for.
- **Consequence for the `PARTY_STATE_MISSING` gate.** That gate is now **effectively unreachable**: with the fallback, a blank business state is caught first by the more specific `BUSINESS_PROFILE_INCOMPLETE`. The branch is kept as defence-in-depth (it is the true statement of the requirement, and it guards a future way for resolution to fail) but the API now reports `BUSINESS_PROFILE_INCOMPLETE`. Tests assert the code that actually fires, so the two cannot silently disagree.
- **Tests:** `WalkInPartyStateTests` (5 cases: walk-in previews, walk-in saves *and* issues, a real party state still wins, nothing resolvable still errors, explicit POS still overrides). Removing the fallback fails 3 of them.


---

#### 1.4.6 Deliberately out of scope

- [ ] Stock deduction / insufficient-stock check → **2.1** (triggers on **issue**).
- [ ] PDF, bank details, signature, reverse-charge constant line → **2.3** (`amount_in_words()` built in 1.4.2).
- [ ] Payments, `paid_amount`, `payment_status` → **3.1**.
- [ ] Credit/debit notes, e-invoicing (IRN/QR), e-way bills, exports/SEZ, compensation cess, nil-rated vs exempt split, **reverse charge**, staff logins (`BusinessProfile.user` stays OneToOne), "save free-text line as item" → later.

---

#### 1.4.7 Decision log (questions raised and how they were resolved)

| Question | Resolution |
|---|---|
| Shipping state source | **Add `Party.shipping_state_code`** (decision 4). |
| Goods vs service detection | **Snapshot `item_type` on the line** (decision 13). |
| Reverse charge | **Deferred; no field** (decision 14). |
| Free-text lines | **Allowed** (decision 15). |
| Staff / audit columns | **`created_by`, `issued_by`, `cancelled_by` now** (decision 16). |
| Backdating | **Allowed with warning** (decision 17). |
| Walk-in party state | **Business state** (decision 9). Implemented later as decision 20, after the step E walk proved it was missing. |

##### Decision 18 — HSN requirement: configurable, default STRICT

> **Superseded by decision 22 (2.0.3):** the `STATUTORY` ₹5,000 mode is removed and replaced by `hsn_min_digits` (4 or 6, by turnover).

- [x] **Question.** The statute requires HSN/SAC only on a B2B invoice above ₹5,000 (GST Notification 12/2017-CT). Should the issue-time gate enforce that threshold literally, or keep demanding an HSN on every line?
- [x] **Resolution: `BusinessProfile.hsn_requirement` with two modes, defaulting to `STRICT` (the current, over-complying behaviour).**
  - `STRICT` — every line of every tax invoice needs a valid HSN/SAC. Over-complies on purpose.
  - `STATUTORY` — gate engages only when the recipient has a GSTIN **and** `grand_total > 5000.00`.
  - A non-`REGULAR` business is exempt in both modes (a Bill of Supply carries no HSN requirement).
  - Default is `STRICT`, so **existing businesses never silently loosen** the gate when the field is added.
- **Why not just implement the ₹5,000 threshold:** it rests on an interpretation of a per-invoice rule that we have not had confirmed. Encoding it as the only behaviour means a wrong reading becomes baked in, with no cheap way back. Keeping it as an opt-in mode means the CA's answer is a settings change, not a business-logic change.
- **Why STRICT is the safe default:** an under-complied tax invoice cannot be fixed after the fact (it needs a credit note and, potentially, a penalty); an extra HSN code on a line is harmless. Over-compliance is the recoverable direction.
- **Server-side only.** The browser must never decide whether a line needs an HSN — that is the same class of mistake as client-side GST maths. The frontend only *warns*.
- ⚠️ Still on the 1.4.8 CA list. Do not switch to `STATUTORY` on the strength of this note alone.

##### Decision 19 — Invoice number width: keep 16 chars, fail loudly at the cliff

- [x] **Question.** `invoice_number` is `CharField(16)` and a maximum-length prefix (4 chars) fills it exactly: `4 + 1 + 5 + 1 + 5 = 16`. Widen the column, or keep it and guard?
- [x] **Resolution: keep 16, and add an explicit series-exhaustion guard (`SERIES_EXHAUSTED`).**
- **The real defect this uncovered.** Python's `%05d` is a **minimum** width, not a fixed one. A series of 100,000 does not stay 5 digits — it becomes `100000`, making the number 17 characters with a 4-char prefix and overflowing the column. Previously that would have surfaced as an unhandled database error roughly 100,000 invoices into a financial year, with no explanation.
- **Why not widen the column:** widening moves the cliff rather than removing it, and invoice numbers have no legal length limit, so widening later is cheap. It also would not have fixed the underlying problem, which is that the failure was silent.
- **Why not truncate:** truncating would mint **duplicate numbers on a legal document**, and duplicates are unrecoverable after filing. Growing the string and failing loudly is the only safe third option — hence the guard.
- **Pinned by test.** `test_the_width_arithmetic_is_pinned` asserts `len(prefix) + 1 + len(fy) + 1 + NUMBER_WIDTH == MAX_NUMBER_LENGTH`, so changing the series width or the FY format fails immediately instead of overflowing silently in the future.
- **When a business really does outgrow it:** ~99,999 invoices is ~274/day for an entire financial year. Realistically the fix is to widen `invoice_number` and/or add an FY-wise series reset, and it should be a deliberate migration — not something the guard papers over silently.

#### 1.4.8 Compliance questions to confirm with a CA before launch

These are ⚖️ points in this plan where we are relying on our own reading of GST rules rather than a
verified source. Each is implemented the *safe* way (over-complying rather than under-complying), so
none of them block the build — but all of them should be checked.

| # | Question | What we implemented meanwhile |
|---|---|---|
| 1 | HSN/SAC digits: confirm 4 digits up to ₹5 cr annual turnover and 6 above, and that there is **no** per-invoice ₹5,000 rule. | Every line needs HSN/SAC of at least `hsn_min_digits` (decision 22). |
| 2 | Confirm the current GST slab structure and that `12%` / `28%` are retired for new invoices (tobacco excepted). | `40` added; 12/28 kept for historical invoices. |
| 3 | Is `0%` being used for both nil-rated and exempt acceptable until we split them? GSTR-1 reports them separately. | Single `0.00` with a code comment. |
| 4 | Confirm the CGST+UTGST list is exactly the five UTs without a legislature. | `{"04","26","31","35","38"}`. |
| 5 | Should round-off be permitted at all for this user's customers/auditors? | Off by default, per business, with the warning text. |
| 6 | Confirm `place of supply` for services uses the recipient's location (s.10(1)(c)/s.12) and that our override hint covers the s.10(1)(b) buyer-directed-delivery case. | Billing state by default + manual override. |
| 7 | Is a data-migration-created `Walk-in / Cash Customer` acceptable, or should cash sales bypass `Party` entirely? | One walk-in party per business. |
| 8 | Compensation cess on luxury goods — out of scope here; confirm no 1.4 invoice needs a cess column. | Not modelled. |
| 9 | Record retention period for invoices and books (I believe 72 months from the annual-return due date). | `PROTECT` on invoice data; no hard deletes (2.0.10). |
| 10 | Statutory cut-off for declaring credit notes against an earlier period, and whether a lock date matches how you file. | Lock date + credit notes dated in the open period (2.0.4, 2.4). |
| 11 | Stock: is a negative-stock billing policy acceptable for your clients' accounts? | `ALLOW` default, `BLOCK` per business (decision 33). |

#### 1.4.9 Post-build review (before Phase 2)

> **Scope of this review.** First written from the design and results recorded in this file, then
> **cross-verified against the code**: 9 findings confirmed, 4 corrected (rows 3, 7, 10, 11 below),
> and 2 added (rows 14, 15). Compliance readings ("probably") still need your CA (see 1.4.8).

**What is right and should not be touched:** decisions 1-16 and 19, the amount-split rounding, the
inclusive-tax extraction (`tax = net - taxable`), server-only tax maths with the preview-drift
tests, the invoice -> counter lock order, snapshots on both invoice and line, the
`SERIES_EXHAUSTED` guard with its pinned width test, and the habit of reintroducing a bug to prove
the test catches it. The two Step B bugs you found are the proof the invariant tests work.

| # | Sev | Finding | Why it matters | Fixed in |
|---|---|---|---|---|
| 1 | **P0** | **Decision 20 is too broad.** Any party with a blank state now falls back to the business state, not just the walk-in. | A real customer with no state silently gets CGST+SGST instead of IGST. `PARTY_STATE_MISSING` became unreachable, which is the symptom of this, not a harmless side effect. | **CLOSED in 2.0.2** (decision 21). |
| 2 | **P0** | **The `STATUTORY` HSN mode encodes a ₹5,000 per-invoice threshold I cannot support.** HSN digit count depends on annual turnover (4 digits, 6 above ₹5 cr) as I understand it. | A business above ₹5 cr passes the 4-digit gate and is under-compliant. An opt-in mode with a wrong number in it is worse than none. | **CLOSED in 2.0.3** (decision 22). |
| 3 | **P0** | **Throttling gaps.** `user` is 600/hour (10/min across the whole API) - too low for autocomplete-heavy billing. **`NUM_PROXIES` is unset** and **`CACHES` is undefined** (falls back to per-process `LocMemCache`). The `login` and `preview` scopes **already exist and are tested** - do not rebuild them. | A cashier hits 600/hour mid-shift; behind Render's proxy all anonymous users can share one bucket; counts reset on every deploy and split across workers. *(Correction: an earlier draft said 300/hour and "scopes unspecified" - both wrong.)* | **PART-CLOSED in 2.0.1**: register + refresh now scoped (`auth`). The cache, `NUM_PROXIES` and rate sizing stay in **2.0.5**. |
| 4 | **P0** | **"Stock-tracked" has two definitions.** `Item.tracks_stock` (property) means `PRODUCT`; `stock_tracked()` (queryset) means `PRODUCT` and non-null stock. | 2.1 would compute `None - qty` for a product saved without stock and return a 500 on issue. | 2.0.6 / decision 25 |
| 5 | **P0** | **No CI.** The concurrency tests need PostgreSQL, a Neon branch and someone remembering to run them. | A regression in the locking code passes every default run. | **CLOSED in 2.0.1** (decision 27) - **pending its first green CI run + branch protection.** |
| 6 | P1 | **Backdating is a warning, not a rule** (decision 17). | An invoice dated into an already-filed GSTR-1 period is never reported. It also leaves "cancel -> issue a credit note" (decision 6) as advice only. | 2.0.4 / decision 23 |
| 7 | P1 | **Constraints exist but are incomplete.** `unique_invoice_number_per_business` and `invoice_item_is_item_backed_or_self_describing` are in place. Missing: `quantity > 0`, `unit_price >= 0`, `line_discount >= 0` (no validators either), lifecycle checks, `grand_total >= 0`. *(Correction: an earlier draft said the `InvoiceItem` admin "is not confirmed read-only". It **is** — `admin.py:68` `has_change_permission` and `:79` `has_delete_permission` both return `False` for non-draft, and the inline sets `can_delete = False` + `max_num = 0`. So 2.0.7 is **constraints only**; do not spend time re-doing the admin.)* | One shell session or admin action bypasses the service layer. | 2.0.7 / decision 26 |
| 8 | P1 | **Nothing re-verifies issued invoices.** | Corruption or a future bug is found by a customer, not by you. | 2.0.8 |
| 9 | P1 | **1.4.5 was ticked "browser smoke test" but verified over HTTP.** | The JS console, layout and print path were not exercised in a browser. | 2.0.9 |
| 10 | P1 | **Only `DISABLE_SERVER_SIDE_CURSORS` is missing.** `conn_max_age=60` and `conn_health_checks=True` are already set. | `.iterator()` (4.2 exports) fails through Neon's pooled URL. **Confirmed live:** both Neon URLs are `-pooler`, so this is not hypothetical. Still **2.0.5** - untouched so far. |
| 11 | **P0** | **The real hole is `TenantModel.business = CASCADE` (`core/models.py`).** `Invoice.party` and `InvoiceItem.item` are already `PROTECT`. | Deleting one `BusinessProfile` silently cascades away every invoice, party, item and (from 2.1) stock movement. | 2.0.10 |
| 12 | P2 | `round_invoice_total` is a live setting, not snapshotted on the invoice. | Audits cannot re-apply today's setting to an old invoice; they must check arithmetic instead (done in 2.0.8). | 2.0.8 |
| 13 | P2 | A shared placeholder mobile (`9876543210`) on the walk-in. | Anything that later sends to a mobile (WhatsApp share, reminders) would message a fake number. | 2.0.2 |
| 14 | P1 | **A docstring in `accounts/urls.py` claims "NUM_PROXIES is configured, so this is a deterrent, not a hard guarantee." It is not configured.** `FUTURE_CHECKLIST.md` A1 correctly says it was deliberately left out. | The comment asserts a safety property that does not exist, and it is the one a future reader will trust. | **CLOSED in 2.0.1** - the docstring is replaced with one that states `NUM_PROXIES` is *not* set and points at 2.0.5. The underlying risk stays open in **2.0.5**. |
| 15 | P1 | **Plan defect in 2.1 (this file):** decision 32 locked the counter last, but `source_label` (`INV/26-27/00012`) only exists after the counter is locked, and the ledger is append-only. | The plan as first written could not satisfy both. | **CLOSED** - decision 32 amended to `reserve_sale`/`commit_sale`. |

---

## Phase 2: Production Hardening, Stock Automation, Purchases, Returns & PDF Engine

> **Status: PLANNED.** 2.0 and 2.1 are specified in full below, in the same format as 1.4
> (locked decisions → steps → tests → out of scope → decision log). 2.2–2.4 are specified at
> **decision level**: the traps we already know about are locked in now so nobody writes the wrong
> code in between, and each is expanded to step level when we reach it.
> ⚖️ marks compliance points reflecting my understanding of GST rules as of mid-2026 — **confirm
> with your CA before launch.**

**Build order. Finish and verify each section before starting the next:**

| Section | Scope | Touches DB? | "Done" means |
|---|---|---|---|
| **2.0** | Close the 1.4 review findings (P0 items first, CI before everything) | yes | CI green on SQLite **and** PostgreSQL; every P0 item ticked |
| **2.1** | Stock ledger, deduction on issue, reversal on cancel, manual adjustments | yes | concurrency + property tests green on PostgreSQL; `reconcile_stock` clean |
| **2.2** | Purchase bills (stock in) | yes | decisions below turned into steps, then built |
| **2.3** | PDF engine & sharing | no | deployment spike passes **before** any template work |
| **2.4** | Credit/debit notes & returns | yes | **launch gate for any B2B customer** |

---

### 2.0 Close the 1.4 review findings

Phase 1.4 works. This section fixes what a post-build review found: two places where a decision
was implemented too broadly, one rule that is probably wrong in law, and several things that are
fine on a developer laptop but break for a real cashier on Render + Neon. **P0** = must be done
before 2.1; **P1** = must be done before any real customer.

#### 2.0.0 Locked decisions (do not re-litigate mid-build)

| # | Decision | Rule | Why |
|---|---|---|---|
| 21 | **Walk-in is a flag, not a name** (narrows decision 20) | `Party.is_walk_in` (bool, default False) + a partial unique constraint: **at most one per business**. The blank-state fallback to the business state applies **only** to `is_walk_in` parties. Its state is **never stored** — resolved from the business at invoice time. Any other party with a blank state → `PARTY_STATE_MISSING` (reachable again). | Decision 20 fixed the symptom (walk-in couldn't be billed) by letting *every* blank-state party fall back to the business state. A real customer with a blank state would now silently get CGST+SGST instead of IGST — a wrong tax type on a legal document, the exact case `PARTY_STATE_MISSING` existed to stop. A stored state also goes stale if the business changes state. Looking the walk-in up by *name* breaks if it is renamed. |
| 22 | **HSN: drop the ₹5,000 mode; digits depend on turnover** (replaces decision 18's `STATUTORY`) | Remove `hsn_requirement`. Replace with `BusinessProfile.hsn_min_digits` (`4` default, `6`). Behaviour stays "HSN/SAC required on every line" (today's `STRICT`). | ⚖️ As I understand it, notification 12/2017-CT set HSN digit counts **by annual turnover**, and 78/2020-CT (from 1 Apr 2021) made it 4 digits up to ₹5 crore and 6 digits above. I know of **no** per-invoice ₹5,000 threshold for HSN (₹5,000 is an old reverse-charge daily limit). A business above ₹5 cr passing today's 4-digit minimum is *under*-compliant, and an opt-in mode labelled `STATUTORY` that encodes a probably-wrong number is a trap. |
| 23 | **Lock date replaces the backdating warning** (amends decision 17) | `BusinessProfile.books_locked_until` (date, nullable). Issue, cancel and draft-save with `invoice_date <= books_locked_until` → `PERIOD_LOCKED`. `invoice_date > today (IST)` → `FUTURE_DATE` at issue. Credit notes (2.4) must be dated **after** the lock date — that is the sanctioned way to correct a filed period. | A UI warning does not stop a back-dated invoice being slipped into an already-filed GSTR-1 period, where it is never reported. It also closes the decision-6 gap ("cancel after filing → issue a credit note" was advice only). The same mechanism every accounting package uses. |
| 24 | **Throttles are sized for a cashier, not an attacker only** | Raise `user` from 600 to >= 5000/hour; keep the existing `login` and `preview` scopes and **add `RegisterView` (`accounts/views.py:23`) and `TokenRefreshView` (`accounts/urls.py:32`)** — both are currently unscoped and fall through to `anon: 60/hour`; `NUM_PROXIES = 1` on Render; throttle counts in a **shared** cache; 429s use the project error format + `Retry-After` and the UI handles them. | 10 requests/minute across the whole API is exhausted by autocomplete alone. With `NUM_PROXIES` unset behind Render's proxy, anonymous requests can share one IP bucket (one attacker blocks every login). A per-process `LocMemCache` resets on every deploy and splits counts across workers. |
| 25 | **`Item.track_stock` is explicit** | New boolean. `SERVICE` ⇒ False. `PRODUCT` default True. DB rule: `track_stock = False OR (item_type = PRODUCT AND current_stock IS NOT NULL)`. `Item.tracks_stock` (property) and `stock_tracked()` (queryset) become one definition: `track_stock`. | Today they disagree: the property says "PRODUCT", the queryset says "PRODUCT and stock not null". A product saved with blank stock is "tracked" to one and "untracked" to the other, and 2.1's `current_stock − qty` on `None` would 500 on issue. Implicit "NULL means untracked" also lets a shop skip opening stock and silently never have stock move. |
| 26 | **The database enforces what the service layer promises** | CheckConstraints for lifecycle and non-negative money/quantity; read-only admin for invoice lines; `audit_invoices` command recomputes issued invoices. | Immutability today lives only in service code and `InvoiceAdmin`. One shell session, admin action or future endpoint bypasses it. Cheap backstops now beat a forensic exercise later. |
| 27 | **CI is a deliverable, and a tick means "verified as written"** | GitHub Actions with a PostgreSQL service container; red = no merge. A checkbox is ticked only for what was actually run. | Concurrency tests only mean something on PostgreSQL, and today they depend on one machine, one Neon branch and someone remembering. 1.4.5 was ticked "browser smoke test" but was verified over HTTP, not in a browser. |

---

#### 2.0.1 CI first (P0) — everything after this is protected by it

- [x] **`.github/workflows/ci.yml`**, on every push and pull request:
  - Job `test-sqlite`: install deps (`uv`), `ruff check`, `python manage.py makemigrations --check --dry-run`, full suite on SQLite.
  - Job `test-postgres`: `services: postgres: image: postgres:16`; set `TEST_DATABASE_URL` to the service; run **only** the concurrency classes with `--settings=config.settings_test_pg`. This removes the Docker/WSL prerequisite on your laptop entirely.
  - Job `contract-smoke`: **`test_contract.py` is a `LiveServerTestCase`**, so it speaks real HTTP over a socket and needs no `runserver`, no throwaway database and no script on disk. (`smoke_contract.py` / `smoke_step_e.py` were temporary and have been deleted.)
  - Step `python manage.py check --deploy` with production-like env (`DEBUG=False`); decide which warnings are accepted and list them in the workflow.
  - Step `pip-audit` — **blocking from day one** on runtime deps; dev deps audited but non-blocking, because a lint-tool CVE is not a production risk and blocking on one is how teams end up disabling the audit.
  - **No production secrets in CI.** Dummy `SECRET_KEY`, no `DATABASE_URL`, no `CLOUDINARY_URL`.
- [ ] Branch protection: make the jobs required checks on `main`. **Needs your GitHub account — see the report.**
- [ ] **Done when (mutation spot-check):** temporarily delete the `select_for_update()` in the issue path — the Postgres job **must fail**. Restore it. A CI job that cannot fail proves nothing. **Written into the workflow as a PR-only guard step, but it can only be *proved* once CI has run once.**

#### 2.0.2 Walk-in as a flag (P0, decision 21)

- [x] `Party.is_walk_in` + `UniqueConstraint(fields=["business"], condition=Q(is_walk_in=True))` → `parties/0004`.
- [x] **Data migration** → `parties/0005_backfill_party_is_walk_in.py`. Flags the oldest qualifying row per business and **renames** any duplicates to `... (duplicate <pk>)` rather than deleting them — they may carry invoices and `Invoice.party` is `PROTECT`, so deleting would fail anyway. Blanks every flagged walk-in's `state_code`. Has a working `backwards`.
- [x] `get_or_create_walk_in_party()` looks up **by flag**, not by name. Pinned by a test that renames the row in the DB and shows the lookup still works.
- [x] `resolve_place_of_supply()`: fallback to `business.state_code` **only if `party.is_walk_in`**; otherwise a blank party state raises `PARTY_STATE_MISSING`. The gate is live again — decision 20's "effectively unreachable" note is replaced with a comment saying it is reachable *because* decision 21.
- [x] **Make the state requirement conditional on the flag (a hard blocker, not a nicety).** `parties/serializers.py` now consults `self.instance.is_walk_in` before raising. Tests: a walk-in can be PATCHed while its state stays blank; a normal party with a blank state is still refused.
- [x] **Protect it:** the walk-in cannot be deleted/soft-deleted, renamed, deactivated, or given a GSTIN (`WALK_IN_PROTECTED`, 400). `destroy()` is overridden so `perform_destroy` can refuse rather than silently no-op; `is_active: false` is caught on both PUT and PATCH, including the `"false"`/`"0"` string forms a form may send. The message is a shared constant, and a test asserts the serializer's and the view's copies stay identical.
- [x] `PartySerializer` exposes `is_walk_in` **read-only** (a PATCH of `false` does not clear it).
- [ ] The UI shows a badge and **hides the placeholder mobile**. *(Backend done; frontend badge lands with the parties screen in 2.0.9.)* Anything that later uses a mobile number (2.3 WhatsApp share, reminders) must treat a walk-in as "no number — ask".
- [ ] Phase 3 exclusion list: walk-in is excluded from overdue/reminder lists and "top customers" (see the 3.2 patch). *Deferred to Phase 3, as planned.*
- [x] **Tests:** walk-in previews, saves and issues with a blank state; a **non-walk-in** with a blank state → `PARTY_STATE_MISSING` at preview **and** at issue (the test decision 20 had to drop); second walk-in rejected by the constraint; rename/delete/GSTIN/deactivate blocked; business state change flows into the next walk-in invoice.

#### 2.0.3 HSN digits instead of the ₹5,000 mode (P0, decision 22)

- [x] Migration: `accounts/0008` adds `hsn_min_digits` (`4` | `6`, default `4`) and removes `hsn_requirement`. Every business now behaves like the old `STRICT`; a business that had switched to `STATUTORY` becomes *stricter*, which is the recoverable direction.
- [x] Gate: every line needs a valid HSN/SAC of **at least** `hsn_min_digits` digits. Errors: `HSN_REQUIRED` (missing), `HSN_TOO_SHORT` (under the minimum) and `HSN_INVALID` (structurally impossible — 5 or 7 digits). Non-`REGULAR` businesses stay exempt. Server-side only.
- [x] Profile UI: replaced the mode dropdown with **"HSN / SAC digits required"** (4 digits / 6 digits), plain-language help text, and a warning to ask a CA. No legal claims.
- [x] Update 1.4.8 CA question 1 and decision 18's note (done in this file).
- [x] **Tests** (20, replacing the old `STATUTORY` suite): 4-digit passes at min 4 and fails at min 6; 6-digit passes at min 6; 8-digit still accepted at min 6; free-text lines; SAC for services; Bill of Supply exempt at either minimum; raising the setting never mutates an already-issued invoice; **the invoice value and the recipient's GSTIN no longer change the requirement at all** (the exact regression decision 22 exists to prevent); the retired field is gone from the schema and cannot change the setting through the API.

#### 2.0.4 Lock date and date rules (P0, decision 23)

- [ ] `BusinessProfile.books_locked_until` (nullable date) + serializer + profile UI: **"Lock books up to"** with help text *"Set this to the last day of the month whose GSTR-1 you have filed."*
- [ ] Rules (service layer, one helper `assert_period_open(business, date)` used everywhere): draft save, issue and cancel → `PERIOD_LOCKED` for `invoice_date <= books_locked_until` (message tells the user to issue a credit note). `FUTURE_DATE` at issue for dates after today in IST.
- [ ] Billing UI: date picker `min` = day after the lock date; field-level message.
- [ ] 2.2 purchases and 2.4 credit notes call the same helper.
- [ ] **Tests:** boundary day (== lock date blocked, +1 allowed); cancel blocked in a locked period; issued-before-lock invoice cannot be cancelled; IST midnight edge; lock date of `None` changes nothing.

#### 2.0.5 Throttles, proxy and connection settings (P0, decision 24)

- [ ] **Do not rebuild what exists:** the `login` scope (`accounts/urls.py`) and `preview` scope (`invoices/views.py`) are wired and tested. Only audit that register and token refresh are covered.
- [ ] Raise `DEFAULT_THROTTLE_RATES["user"]` from `600/hour` to >= `5000/hour`.
- [ ] **`NUM_PROXIES = 1`** (Render sits behind one proxy). **Verify on the deployed service** by logging `HTTP_X_FORWARDED_FOR` once from a staging request - a wrong value is silent.
- [ ] **Fix the false docstring in `accounts/urls.py`** so it states the real behaviour, and treat **`FUTURE_CHECKLIST.md` A1 as live and open** until `NUM_PROXIES` is set.
- [ ] **Define `CACHES`** with `DatabaseCache` (`python manage.py createcachetable`; no Redis needed on the free tier). Not `LocMemCache`.
- [ ] 429 response uses the project error format and `Retry-After`. Frontend (`/preview/`, item/party autocomplete): show "Too many requests - retrying in N s" and retry; never leave blank or stale totals.
- [ ] Neon: `conn_max_age=60` and `conn_health_checks=True` are **already set - leave them**. Add only **`DISABLE_SERVER_SIDE_CURSORS = True`** for the pooled (`-pooler`) URL (transaction pooling breaks server-side cursors, which `.iterator()` uses).
- [ ] **Tests:** 429 shape and `Retry-After`; 300 autocomplete requests in a burst are **not** throttled; `.iterator()` over a few thousand rows works through the pooled URL; the throttle cache is not `LocMemCache`.

#### 2.0.6 Explicit stock tracking on `Item` (P0, decision 25 — prerequisite for 2.1)

- [ ] Migration: add `track_stock`. Backfill: `PRODUCT` with non-null stock → True; `PRODUCT` with null stock → False; `SERVICE` → False.
- [ ] `CheckConstraint`: `track_stock = False OR (item_type = 'PRODUCT' AND current_stock IS NOT NULL)`.
- [ ] Unify `Item.tracks_stock` (property) and `Item.objects.stock_tracked()` on `track_stock`; `is_low_stock` and `low_stock` filters use it.
- [ ] Serializer: `SERVICE` forces `track_stock=False` and null stock fields; `PRODUCT` defaults True and requires `current_stock >= 0` (default 0) when tracked; null when not tracked.
- [ ] Items UI: **"Track stock for this product"** checkbox (default on); stock inputs shown only when on.
- [ ] **Tests:** each `item_type × track_stock × stock` combination; constraint at DB level; the former "PRODUCT with null stock" now round-trips as untracked; low-stock filter excludes untracked.

#### 2.0.7 Database backstops and read-only history (P1, decision 26)

- [ ] **Already present - keep:** `unique_invoice_number_per_business`, `invoice_item_is_item_backed_or_self_describing`.
- [ ] Pre-flight: a read-only query listing any existing rows that violate the new constraints; the migration raises a clear message instead of failing halfway.
- [ ] `Invoice` CheckConstraints: `status = 'DRAFT' OR (invoice_number != '' AND issued_at IS NOT NULL)`; `status != 'CANCELLED' OR (cancelled_at IS NOT NULL AND cancellation_reason != '')`; `grand_total >= 0`.
- [ ] `InvoiceItem`: `quantity > 0`, `unit_price >= 0`, `line_discount >= 0` as **constraints and as serializer/model validators** (today neither exists). `InvoiceCounter.last_number >= 0`.
- [ ] `InvoiceItemAdmin`: read-only and no delete when the parent invoice is not a draft; `has_delete_permission` false for issued/cancelled invoices.
- [ ] **Tests:** each constraint rejects a violating row via the ORM (`bulk_create`/`update`), bypassing serializers.

#### 2.0.8 Integrity audit command (P1, decision 26)

- [ ] `python manage.py audit_invoices [--business ID] [--fy 2026-27]`, **exit code 1 on any finding** (so cron/CI can alert). For every issued/cancelled invoice:
  1. Recompute totals from the **stored line inputs** with `calculate_invoice()` and compare every stored money field exactly. (`round_invoice_total` is not snapshotted — so verify `grand_total == Σ lines + round_off`, `|round_off| ≤ 0.50`, and `grand_total` is a whole rupee whenever `round_off ≠ 0`, instead of re-applying today's setting.)
  2. `Σ line.* == invoice.*` for taxable, CGST, SGST, IGST; per line `cgst + sgst + igst == tax`.
  3. Number series: no duplicates, **no gaps** within a business + FY (gaps mean a bug — cancelled numbers are never freed), `InvoiceCounter.last_number == max(number)`.
  4. `supply_type` consistent with `business_state_code` vs `place_of_supply`; `Bill of Supply` invoices carry zero tax.
- [ ] Run it in CI against the test dataset, and later as a weekly Render cron.
- [ ] **Tests:** a clean dataset passes; each deliberately corrupted field is reported with invoice number and field name.

#### 2.0.9 Real-browser verification of 1.4.5 (P1, decision 27)

- [ ] `docs/QA_CHECKLIST.md` — manual, run in a real browser (desktop + a phone-width window): walk-in sale; inter-state B2B; free-text transport line; inclusive-price invoice; draft → issue → cancel → copy; Print preview; 429 and cold-start behaviour (first request after idle); **no errors in the JS console** on any page; keyboard-only billing.
- [ ] Optional Playwright smoke for the top three flows, run in CI (non-blocking at first).
- [ ] Amend the 1.4.5 "Done when" line to say *verified over HTTP; real-browser pass pending* until this checklist is signed off.

#### 2.0.10 Data retention and deletion safety (P0)

- [ ] **The real hole:** `TenantModel.business` is `on_delete=CASCADE` in `core/models.py`, so deleting a `BusinessProfile` cascades away every tenant row. `Invoice.party` and `InvoiceItem.item` are already `PROTECT` - leave them.
- [ ] Change `TenantModel.business` to **`PROTECT`**. This generates `AlterField` migrations on every tenant model but **no SQL**, because `on_delete` is enforced in Python. Also verify `BusinessProfile.user`'s `on_delete`.
- [ ] Find tests or code that call `business.delete()` or `user.delete()` and change them (use deactivation or fixtures). "Close account" = deactivate + export; admin delete disabled for any `BusinessProfile`/`User` that owns invoices.
- [ ] ⚖️ Write down the retention period (my understanding: GST records are kept for 72 months from the due date of the annual return) and confirm with your CA (1.4.8 #9).
- [ ] **Tests:** deleting a business or user that owns an issued invoice raises `ProtectedError`; the same for a business with only parties or items.

#### 2.0.11 Multiple logins — device tracking and revocation (built alongside 2.0.1, decision 41)

> **Not in the original 2.0 plan.** Raised during the 2.0.1 build: the project had no
> logout, no session list, and — the actual hole — **no way to invalidate a refresh token.**

##### Decision 41 — a session is a row keyed by the refresh token's `jti`

| # | Decision | Rule | Why |
|---|---|---|---|
| 41 | **A session is a row keyed by `jti`, revocable individually** | Install `rest_framework_simplejwt.token_blacklist`; add `UserSession(user, jti, device, ip, user_agent, created_at, last_used_at, revoked_at)`. Revoking blacklists that one `jti`. `BLACKLIST_AFTER_ROTATION = True`, and the rotated token inherits the session row. | A refresh token lived **7 days and nothing could invalidate it**. "Logout" only deleted the copy in the browser, so a stolen or borrowed device kept access for a week with no way to cut it off, and a user could not see they were signed in on three devices. Keying on `jti` means "revoke this device" leaves the others alone - changing the password logs you out everywhere, which is far too blunt for a shop owner. |

**What was built (2.0.1, all tests green):**

- [x] `token_blacklist` installed; `BLACKLIST_AFTER_ROTATION = True`. Without the latter, rotation retires the old token but leaves it valid - "log out everywhere" could never invalidate anything.
- [x] `UserSession` model (`accounts/0009`). Keyed by `jti`, unique; no token secret is stored in our table (the token itself stays in `OutstandingToken`).
- [x] `accounts/sessions.py` — `describe_device` (browser + platform from the User-Agent), `client_ip`, `record_login`, `attach_session`, `revoke_session`, `revoke_all_except`, `active_sessions`.
- [x] Endpoints: `GET /auth/sessions/`, `DELETE /auth/sessions/{id}/`, `POST /auth/sessions/revoke-others/`, `POST /auth/logout/`.
- [x] Login **and register** both record a session, so a freshly registered device is visible immediately rather than appearing only at the next login.
- [x] **Finding #3 closed:** `RegisterView` and `TokenRefreshView` had **no `throttle_scope`** and fell through to `anon: 60/hour`. All three unauthenticated auth endpoints now share one `auth` scope at 10/minute.
- [x] Finding #14's false docstring replaced in the same file.

**Three decisions inside this that are worth not re-litigating:**

- **`LogoutView` is `AllowAny`.** The refresh token *is* the credential it consumes. Requiring a valid access token as well would make logout impossible exactly when it matters - when the access token has expired.
- **`client_ip` reads `REMOTE_ADDR`, not `X-Forwarded-For`.** Behind Render the forwarded header is client-controlled until `NUM_PROXIES` is set (2.0.5), and this value is shown as "where you signed in". A spoofable value would be worse than the proxy's own address.
- **Access tokens are NOT revocable.** They are stateless, so one already in the wild stays valid for up to 30 minutes. Revoking a refresh token bounds the damage to that lifetime rather than removing it. That is inherent to JWTs, not a shortcut - so the UI must not promise an instant cut-off.

- [ ] Frontend: a "Signed-in devices" screen, and a real logout button that calls `/auth/logout/` instead of only deleting the token locally. **Not built** - the backend is done and tested, but nothing calls it yet, which means from a user's point of view logout is still cosmetic.
- [ ] Old sessions predating this migration have no row, so they will not appear in the list and cannot be revoked. Acceptable for pre-launch; a "revoke all" that blacklists every `OutstandingToken` for the user would be the sweep if it ever matters.

**2.0 is complete when:** CI is green and protected, every P0 box is ticked, the 1.4.9 findings table has no open P0, and `audit_invoices` exits 0 on your dev database.

---

### 2.1 Stock Automation — ledger, deduction on issue, reversal on cancel

The most dangerous kind of bug here is silent: a stock number that is wrong by 3 units, with no
record of why. So the design is an **append-only ledger** (`StockMovement`) and `Item.current_stock`
is only a cached balance of it. Every unit that ever moved has a row saying when, why, who, and
what the balance became.

**Build order:**

| Section | Step | Scope | Touches DB? | "Done" means |
|---|---|---|---|---|
| **2.1.1** | A | Schema: `StockMovement`, profile setting, opening-balance backfill, read-only stock on Item API | yes | migrations clean; `reconcile_stock` exits 0 right after migrating |
| **2.1.2** | B | Pure planning logic (`stock_plan.py`) | **no** | `SimpleTestCase` suite green |
| **2.1.3** | C | Ledger service, adjustments, `reconcile_stock` | yes | service tests green on SQLite |
| **2.1.4** | D | Wire into issue/cancel, API, preview advisory, concurrency | yes | concurrency + property tests green **on PostgreSQL in CI** |
| **2.1.5** | E | Frontend | — | QA checklist extended and signed off in a real browser |

#### 2.1.0 Locked decisions (do not re-litigate mid-build)

| # | Decision | Rule | Why |
|---|---|---|---|
| 28 | **The ledger is the source of truth** | `StockMovement` is append-only (no update/delete in ORM, API or admin). `Item.current_stock` is a **cache** changed only by `apps/inventory/services/stock.py`. Corrections are new movements. | A bare counter can't answer "why is it 7?". An append-only ledger can, and can always be re-derived and verified (`reconcile_stock`). |
| 29 | **No foreign key from the ledger to invoices** | `StockMovement` carries `source_type`, `source_id`, `source_line_id` and a `source_label` text snapshot (e.g. `INV/26-27/00012`). | `invoices.InvoiceItem → inventory.Item` already exists; an FK back creates a **circular migration dependency** between the apps, a classic Django trap. `inventory` must not import `invoices`. The service takes plain `StockLine(line_id, item_id, quantity)` values. |
| 30 | **Idempotency in the database** | `UniqueConstraint(business, movement_type, source_type, source_line_id)` where `source_line_id IS NOT NULL`. | A retried request, double-click or replay can never deduct twice — the second insert is rejected, not "usually prevented". |
| 31 | **Stock moves on ISSUE and CANCEL only** | Drafts and `/preview/` never touch stock. Preview shows an **advisory** availability badge only. | Matches decision 6; a draft is not a sale. |
| 32 | **Global lock order: invoice -> items (ascending `id`) -> counter; stock is reserved before the number exists and written after it** (amends decision 11) | Stock is split into two phases inside one transaction. **Phase 1 `reserve_sale`:** lock items `ORDER BY id`, check sufficiency per policy, compute the plan - writes nothing. Then lock the counter and allocate the number. **Phase 2 `commit_sale(plan, label)`:** insert the movements with `source_label = invoice number` and update the cached balances. | Two invoices selling A,B and B,A deadlock if items lock in line order. Locking the counter last means a failed stock check never touches it. But the ledger is append-only and its label needs the invoice number, so rows cannot be written before the number is known. Two-phase keeps both benefits; the alternatives each lose one (counter before items loses the contention benefit; PK-based labels lose the human-readable reference). |
| 33 | **Negative-stock policy per business; default `ALLOW`** | `BusinessProfile.negative_stock_policy` = `ALLOW` / `BLOCK`. `ALLOW` issues the invoice, writes the movement and returns `stock_warnings`. `BLOCK` rejects with `INSUFFICIENT_STOCK`. | Most shops' opening stock is wrong on day one. A billing app that refuses to bill leaves a customer waiting at the counter and pushes the shopkeeper back to a paper bill — worse for the books than a temporarily negative count. `BLOCK` is one setting away for businesses that want it. *(Your call — both modes are tested; flipping the default is one line.)* |
| 34 | **Item stock is never edited directly** | `current_stock` is read-only on the Items API after creation. Creating a tracked item writes an `OPENING` movement. Changes go through `POST /items/{id}/adjust-stock/` with a reason. A PUT/PATCH that *changes* the value → `STOCK_READ_ONLY` (same value is tolerated, since forms re-send the whole object). | Otherwise the existing edit form silently bypasses the ledger and `reconcile_stock` fails for ever after. |
| 35 | **Cutover, and cancel reverses the ledger — not the invoice** | Stock counting begins at migration time. Invoices issued before 2.1 never deducted and never will. Cancel creates reversals **from that invoice's existing `SALE` movements**; if there are none, it does nothing to stock. | "Restore the quantities on the invoice lines" would, for a pre-2.1 invoice, *add* stock that was never removed. |
| 36 | **Cancel never fails because of stock** | A reversal only adds stock; it is allowed under both policies. | A shopkeeper must always be able to void a wrong bill. (Purchase cancel is different — see 2.2.) |
| 37 | **Which lines move stock** | Only lines with a non-null `item`, snapshot `item_type == PRODUCT`, and `item.track_stock`. Free-text and service lines are skipped silently. Quantity is the line quantity in the item's own unit — **no unit conversion** in 2.1. | Decision 13/15. Item-backed lines copy `unit` from the item; selling in dozens against stock in pieces needs a conversion model that is out of scope. |
| 38 | **Sufficiency is checked per item, not per line** | One invoice with the same item on three lines is checked on the **sum**; movements are still written per line for traceability. | Three lines of 4 against stock 10 each look fine alone and sell 12. |
| 39 | **An item's unit is locked once it has movements** | Changing `unit` on an item with ledger history → `UNIT_LOCKED`. | Changing PCS → KG silently reinterprets every historical quantity. |
| 40 | **Turning tracking off/on is itself a movement** | Disabling writes a closing `ADJUSTMENT` (reason `TRACKING_DISABLED`, brings the ledger sum to 0) and nulls the balance. Enabling writes a new `OPENING`. | Keeps the invariant `Σ movements == current_stock` (or `0` when untracked) true in every state. |

---

#### 2.1.1 Step A — Schema and migrations

- [ ] **`BusinessProfile.negative_stock_policy`** — `ALLOW` / `BLOCK`, default `ALLOW`, serializer + profile UI (with a plain-language explanation of both).
- [ ] **`StockMovement`** in `apps/inventory` (inherits `TenantModel`):
  - `item` FK → `PROTECT`.
  - `movement_type`: `OPENING`, `SALE`, `SALE_REVERSAL`, `PURCHASE`, `PURCHASE_REVERSAL`, `ADJUSTMENT`, `SALES_RETURN`, `PURCHASE_RETURN` (the last two reserved for 2.4).
  - `quantity_change` `Decimal(12,3)` **signed**, never 0. `stock_after` `Decimal(12,3)` (may be negative under `ALLOW`).
  - `source_type`: `INVOICE`, `PURCHASE`, `CREDIT_NOTE`, `MANUAL`, `SYSTEM`; `source_id`, `source_line_id` (`PositiveBigIntegerField`, nullable); `source_label` `CharField(32)`.
  - `reason` (adjustments): `COUNT_CORRECTION`, `DAMAGED`, `LOST_OR_THEFT`, `EXPIRED`, `TRACKING_DISABLED`, `RECONCILIATION`, `OTHER`; `note` `CharField(255)` (**required when `reason = OTHER`**).
  - `created_by` FK → User (`SET_NULL`, `related_name="+"`), `created_at`.
  - **Constraints:** `quantity_change != 0`; sign by type (`SALE` and `PURCHASE_REVERSAL` < 0; `SALE_REVERSAL` and `PURCHASE` > 0; `OPENING`/`ADJUSTMENT` either sign); the idempotency unique constraint (decision 30).
  - **Indexes:** `(business, item, id)` for history and reconciliation; `(business, source_type, source_id)`.
  - `save()` on an existing row and `delete()` raise; admin is read-only with no add/delete.
- [ ] **Backfill (data migration):** for every tracked item with `current_stock != 0`, write one `OPENING` movement (`quantity_change = stock_after = current_stock`, note *"Opening balance at stock-ledger cutover"*).
- [ ] **Items API / serializer:** `current_stock` read-only on update (`STOCK_READ_ONLY` when changed); on create with stock > 0, write the `OPENING` movement in the same transaction; block `unit` changes when movements exist (`UNIT_LOCKED`); handle `track_stock` toggles per decision 40.
- [ ] `makemigrations --check` clean on both databases.
- [ ] **Done when:** `reconcile_stock` (built in 2.1.3) exits 0 immediately after migrating a copy of your dev data — write the data-migration test now with the invariant checked inline.

#### 2.1.2 Step B — Pure planning logic (no DB)

- [ ] **`apps/inventory/services/stock_plan.py`** — imports no model or ORM symbol; all tests are `SimpleTestCase`.
  - `StockLine(line_id, item_id, quantity)` dataclass.
  - `plan_sale(lines)` → per-line movement requests (`quantity_change = −quantity`) **plus** `per_item_total`, both ordered by `item_id` then `line_id`. Rejects non-positive quantities and more than 3 decimals.
  - `check_sufficiency(per_item_total, balances, policy)` → `shortages` under `BLOCK` (`{item_id, requested, available}`), `warnings` under `ALLOW` (items whose balance would go below 0, with the resulting balance). Exactly-equal stock is **not** a shortage; `0.001` short is.
  - `plan_reversal(existing_sale_movements, existing_reversals)` → reversal requests only for sale rows with no reversal yet.
- [ ] **Tests:** same item on several lines; zero/negative/over-precise quantities rejected; ordering deterministic; boundary equality; `ALLOW` vs `BLOCK` outputs; reversal of nothing; reversal skips already-reversed rows; 3-decimal quantities never go through `float`.

#### 2.1.3 Step C — Ledger service (DB)

- [ ] **`apps/inventory/services/stock.py`**
  - `reserve_sale(business, lines)` - **asserts it is inside an atomic block**. Locks all involved items `select_for_update().order_by("id")`; verifies every item belongs to `business` (defence in depth); rejects inactive items (`ITEM_INACTIVE`); skips untracked items; checks sufficiency per policy (raises `InsufficientStock`). Returns a `SalePlan`. **Writes nothing.**
  - `commit_sale(business, user, invoice_id, invoice_label, plan)` - asserts atomic; writes the movements with a correct running `stock_after` per item (several lines of one item chain correctly); updates each `Item.current_stock` once; **idempotent** - a line that already has a `SALE` row is skipped, not doubled.
  - `reverse_sale(business, user, invoice_id)` — derives reversals from the invoice's own ledger rows (decision 35). Never raises for stock reasons.
  - `adjust_stock(business, user, item_id, *, mode="SET"|"CHANGE", quantity, reason, note)` — locks the item; returns the movement.
  - `set_tracking(business, user, item, enabled, opening_quantity=None)` — decision 40.
  - Exceptions: `InsufficientStock(shortages)`, `ItemInactive(item_ids)`.
- [ ] **`python manage.py reconcile_stock [--business ID] [--fix]`** — compare each tracked item's `current_stock` with `Σ quantity_change`; **exit 1** on any drift and print item, cached, ledger. `--fix` never edits the cache silently: it writes an `ADJUSTMENT` with reason `RECONCILIATION`.
- [ ] **Tests (SQLite):** exact movement rows and balances; idempotency (apply twice → one set of rows); reversal twice → one reversal; reversal for a pre-cutover invoice → no-op; `stock_after` chain across lines of one item; foreign-tenant item id rejected; inactive item rejected; ledger rows immutable (update/delete raise); constraint tests (zero quantity, wrong sign, duplicate source line); `reconcile_stock` detects a hand-corrupted `current_stock` and `--fix` records an adjustment.

#### 2.1.4 Step D — Wire into issue/cancel and the API

- [ ] **Issue order (one `@transaction.atomic`):** lock invoice -> status check -> recalc and gates (incl. 2.0.4 `PERIOD_LOCKED`) -> **`reserve_sale`** (locks items ascending, checks, no writes) -> lock counter -> allocate number -> **`commit_sale(..., label=number)`** -> snapshot -> save. Call-site comment: *the counter is locked after the stock check so a failed check never contends on it; rollback undoes the whole issue, so no number is burned and no stock is touched. The movements are written after the number exists because the ledger is append-only and carries the invoice number.*
- [ ] **Cancel:** lock invoice → `reverse_sale` → mark cancelled, in the same transaction; the lock-date rule still applies.
- [ ] **Issue response** carries `stock_warnings: [{item_id, item_name, stock_after}]` when `ALLOW` drove a balance negative.
- [ ] **Errors** (same envelope as the 1.4.4 gates): `400 INSUFFICIENT_STOCK` with `details: [{item_id, item_name, requested, available, unit}]` and a message such as *"Only 3 PCS of Item X in stock (needs 5)."*; `400 ITEM_INACTIVE`.
- [ ] **`/invoices/preview/`** adds an advisory, non-locking `stock_check` per tracked line (`available`, `will_go_negative`). It is never authoritative; the issue is.
- [ ] **New endpoints:** `GET /items/{id}/stock-history/` (paginated; filters `movement_type`, dates); `POST /items/{id}/adjust-stock/`; `GET /stock/movements/` (business-wide, same filters + `item`); `GET /items/?negative_stock=true`. All tenant-scoped via `TenantModelViewSet`; no update/delete routes exist for movements.
- [ ] **Tests (SQLite):** issue deducts exactly; services, free-text and untracked items untouched; same item on two lines aggregated for the sufficiency check; **`BLOCK` failure leaves everything unchanged** — invoice still `DRAFT`, `InvoiceCounter.last_number` unchanged, stock unchanged, zero movements; `ALLOW` goes negative with warnings; cancel restores exactly; cancel twice → no double restore; pre-cutover invoice cancel leaves stock alone; soft-deleted item at issue; `PERIOD_LOCKED`; `STOCK_READ_ONLY`; `UNIT_LOCKED`; tenant isolation for history, movements and adjust-stock (cross-tenant item → 404); **literal-URL tests** (never `reverse()`).
- [ ] **Concurrency tests (PostgreSQL, run in CI — `TransactionTestCase` + threads):**
  - two invoices racing for the **last unit** under `BLOCK` → exactly one succeeds, the other gets `INSUFFICIENT_STOCK`, stock is `0` (never `−1`), only one number consumed;
  - invoices with items in **opposite line order** (A,B vs B,A) → both complete, no deadlock;
  - issue vs `adjust-stock` on the same item → ledger and cache agree afterwards;
  - cancel fired twice concurrently → exactly one reversal;
  - a 20-thread mixed storm of issues, cancels and adjustments, then `reconcile_stock` **and** `audit_invoices` both exit 0.
  - Skip with a loud message when the backend has no `select_for_update` (as in 1.4.4).
- [ ] **Property test:** a seeded random sequence of issue / cancel / adjust operations keeps `current_stock == Σ movements` after every step, and under `BLOCK` never goes below zero. Print the seed on failure.

#### 2.1.5 Step E — Frontend

- [ ] **Items page:** stock column read-only; **"Adjust stock"** modal (Set to / Change by, reason, note; shows *before → after*); **"Stock history"** drawer (type badge, signed quantity, running balance, source label); "Track stock" checkbox and opening-stock field (create only); unit-locked message; a negative-stock banner when any item is below zero.
- [ ] **Billing form:** per-line stock badge from the item search payload; amber warning when quantity exceeds available (*"stock will go to −2"*); under `BLOCK` a red *"will be rejected"* — the server still decides. Handle `INSUFFICIENT_STOCK` by highlighting the offending lines using `details`; after a successful issue, toast any `stock_warnings`.
- [ ] **Profile:** negative-stock policy toggle with the explanation from decision 33.
- [ ] **Done when:** extend `docs/QA_CHECKLIST.md` and sign it off **in a real browser**: sell the last unit from two browser tabs; adjust stock; cancel and see it restored; read the history; run `reconcile_stock` and `audit_invoices` afterwards.

#### 2.1.6 Deliberately out of scope

- [ ] Purchases / stock-in → **2.2**. Sales returns, debit notes → **2.4**.
- [ ] **Stock valuation and COGS** (FIFO / weighted average). Until this exists, any "net profit" figure in 3.3 is a cash-basis estimate and must be labelled so (see the 3.3 patch).
- [ ] Batch / expiry / serial numbers, multiple godowns, stock transfers, unit conversion (dozen ↔ piece), reorder suggestions, barcode scanning, bills of materials.

#### 2.1.7 Decision log

| Question | Resolution |
|---|---|
| Plain counter or ledger? | **Ledger**; `current_stock` is a cache (decision 28). |
| FK from ledger to invoice? | **No** — avoids a circular migration dependency; label snapshot instead (29). |
| Negative stock default | **`ALLOW`**, `BLOCK` per business (33). *Open to your call.* |
| Direct edits of `current_stock` | **Read-only**; adjust-stock endpoint (34). |
| Invoices issued before 2.1 | **Never deducted, never restored** (35). |
| Cancel vs stock | **Never blocked** (36). |
| Lock order | **invoice -> items asc -> counter; reserve before the number, write after** (32, amended). |

---

### 2.2 Purchase Management Module — decision level (expand to steps when reached)

These are locked now because each one prevents a known mistake.

- [ ] **Lifecycle mirrors invoices:** `DRAFT` → `POSTED` → `CANCELLED`. Stock increases on **POST** through the same ledger (`PURCHASE` / `PURCHASE_REVERSAL`, same lock order, same idempotency).
- [ ] **Amounts are entered as printed on the supplier's bill**, not computed. The supplier's rounding can differ by a paisa and your input-tax credit must match their return. `calculate_invoice()` becomes a *validator*: a mismatch above ₹1 shows a warning, it does not overwrite.
- [ ] **Calculator needs an `origin_state` parameter** (supplier state for purchases; place of supply = the business state). Refactor backwards-compatibly: sales keep `origin = business`.
- [ ] **Duplicate-entry guard:** unique `(business, supplier, supplier_bill_number, FY)`. Optional internal reference series `PUR/YY-YY/NNNNN`.
- [ ] **`itc_eligible` per line** (default True), so 4.2 can total claimable credit later; blocked-credit rules are out of scope.
- [ ] **Cancelling a bill reverses an increase**, so it can drive stock negative: it obeys `negative_stock_policy` (`CANCEL_WOULD_GO_NEGATIVE` under `BLOCK`). It also obeys the lock date (2.0.4).
- [ ] **No silent price overwrite:** `Item.purchase_price` is updated only if the user ticks "update item purchase price".
- [ ] **Supplier attachments:** private storage with authenticated URLs; PDF/JPG/PNG, ≤ 5 MB.
- [ ] **Free-text lines** (freight, misc) allowed; they never touch stock.
- [ ] **Composition / unregistered suppliers** issue bills with no tax and give no input credit: warn when the supplier has no GSTIN. `Party` has no registration type yet — add one when this is built.
- [ ] **Reverse charge on purchases** and debit notes for purchase returns → deferred (2.4).

### 2.3 PDF Generation Engine & Document Sharing — decision level

- [ ] **Deployment spike first, before any template work.** WeasyPrint needs system libraries (Pango/Cairo) that Render's native Python runtime does not provide — it needs a Docker deploy. ReportLab and xhtml2pdf are pure Python. Deploy a one-page PDF to your actual Render service and pick the engine from the result; also measure peak memory against the 512 MB free-tier limit.
- [ ] **Fonts:** Helvetica has no `₹` glyph (you get a box). Bundle a TTF with U+20B9 (e.g. Noto Sans) and a Devanagari font; test a party name in Hindi.
- [ ] **Render from stored snapshots only.** Never recompute tax, never read the live business, party or item. A reprint must show identical numbers forever.
- [ ] **Do not store PDFs.** Generate on demand from the snapshots. This removes `pdf_file_url`, the Cloudinary privacy exposure, drift and cleanup. (Replaces the 2.3.2 "save to Cloudinary" step.)
- [ ] **Sharing = signed, expiring link:** `django.core.signing` token (default 7 days), a public throttled endpoint that returns a uniform 404 for any bad token, and `Invoice.share_version` so "revoke links" is one increment. WhatsApp button uses the party mobile; **a walk-in has none** — ask.
- [ ] **Layouts:** A4 Tax Invoice and Bill of Supply first; an **80 mm thermal receipt** for retail/walk-in as a follow-up. ⚖️ Optional "Original for recipient / Duplicate for supplier" labels (Rule 46).
- [ ] **Content:** reverse-charge constant line, `state_tax_label` (SGST/UTGST), amount in words, place of supply name + code, optional bank details / UPI QR / signature image. **No item photos** (bigger files, slower, and a replaced image would rewrite history).
- [ ] **Robustness tests:** HTML escaping (`<script>`, `&` in names), very long item names, a **150-line invoice** (header repeats, totals never split across pages), Hindi text, peak memory, a generation timeout.

### 2.4 Credit Notes, Debit Notes & Sales Returns — decision level (**launch gate for any B2B customer**)

Cancellation stops being legal advice once a period is filed (2.0.4 now blocks it). Returns,
post-sale discounts and price corrections are routine in B2B trade, so no business with registered
customers should go live without this.

- [ ] ⚖️ **Own series** (`CN/YY-YY/NNNNN`) with the same counter, locking and ≤16-character rules as invoices.
- [ ] **Linked to the original invoice**; full or per-line partial; quantity ≤ *remaining returnable* (original − prior notes).
- [ ] **Tax is reversed pro-rata from the original invoice's stored line rates** — never recomputed from today's item or rate.
- [ ] **Dated in the current open period** (after the lock date) — the sanctioned way to correct a filed period. ⚖️ Confirm the statutory cut-off for declaring credit notes with your CA.
- [ ] **Stock:** returned goods restore stock through `SALES_RETURN`; a "damaged — do not restock" flag writes no movement. Value-only notes (post-sale discount) never touch stock.
- [ ] **Debit notes:** upward price revisions, and purchase returns (`PURCHASE_RETURN` decreases stock).
- [ ] Affects party ledger (3.2) and receivables (3.1); reported in GSTR-1 credit/debit note tables (4.2).

---

## Phase 3: Financial Ledgers, Payments & Business Analytics

### 3.1 Payment Tracking (Payments In & Payments Out)
- [ ] **3.1.1 Data Schema & API**
  - [ ] Build `PaymentRecord` Model:
    - Business FK, Party FK.
    - Transaction Type (`PAYMENT_IN` from customer, `PAYMENT_OUT` to supplier).
    - Linked Invoice FK / Purchase FK (Optional for partial/advance payments).
    - Payment Date, Payment Mode (`CASH`, `BANK_TRANSFER`, `UPI`, `CHEQUE`).
    - Amount Paid, Reference Number (UPI Ref / Cheque No).
    - Notes / Remarks.
  - [ ] Implement `POST /api/v1/payments/` API endpoint. Payments may only link to `ISSUED` invoices (reject `DRAFT`/`CANCELLED`); lock the invoice row when recalculating `paid_amount`.
  - [ ] Payment Ledger Logic: Automatically recalculate linked Invoice `paid_amount` and `payment_status` (`UNPAID` -> `PARTIAL` -> `PAID`). `balance_due` must net off credit notes (2.4); payments dates obey the lock date (2.0.4).
  - [ ] Build UI form to record payments received/made with automatic status indicators on invoice lists.

### 3.2 Party Statements & Ledgers
- [ ] **3.2.1 Ledger Computation Engine**
  - [ ] Implement Party Ledger Service (`services/ledger_engine.py`):
    - Retrieve all Sales Invoices, Purchase Bills, Payments In, and Payments Out for a specific party in chronological order.
    - Compute running account balances: Balance = Opening Balance + Invoices - Payments Received.
    - Use `Party.opening_balance_signed` (a `Decimal`) — never convert to `float`.
    - **Exclude `Party.is_walk_in`** from overdue/reminder lists and "top customers"; credit/debit notes (2.4) are ledger entries too.
  - [ ] API Endpoint (`GET /api/v1/parties/{id}/statement/?start_date=...&end_date=...`).
  - [ ] Build UI Statement View: Displays statement table with filtering by date range, total billed amount, total paid, pending balance, and export to PDF.

### 3.3 Business Analytics Dashboard
- [ ] **3.3.1 Summary Aggregations API**
  - [ ] Implement `/api/v1/dashboard/stats/` API endpoint returning aggregated statistics:
    - **Total Sales Amount** (Current month vs overall).
    - **Total Purchase Amount**.
    - **Total Receivables** (Money owed by customers).
    - **Total Payables** (Money owed to suppliers).
    - **Estimated Net Profit** (Sales - Purchases - Expenses). ⚠️ This is a cash-basis estimate, **not** profit: without stock valuation / COGS (out of scope in 2.1.6) it ignores opening and closing stock. Label it "Estimated" in the UI.
  - [ ] Implement `/api/v1/dashboard/low-stock/` returning items where `current_stock <= min_stock_threshold`. **Filter with `Item.objects.stock_tracked()` so services never appear.**
  - [ ] Build Main Dashboard Web View with clean metric cards, recent transaction tables, and low-stock alert widgets.

---

## Phase 4: Advanced GST Compliance, Expense Tracking & Mobile API

### 4.1 Expense Management Module
- [ ] **4.1.1 Expense Data Schema & API**
  - [ ] Build `ExpenseCategory` Model (Rent, Utilities, Salaries, Transport, Office Supplies).
  - [ ] Build `Expense` Model (Business FK, Category FK, Date, Amount, Tax Paid, Payment Mode, Notes, Receipt Attachment).
  - [ ] Implement API endpoints (`/api/v1/expenses/`).
  - [ ] Build UI page to log operational expenses and track business overheads.

### 4.2 GST Reports & Tax Filing Exports
- [ ] **4.2.1 GSTR-1 & GSTR-3B Summary Engine**
  - [ ] Implement `services/gst_reports.py`:
    - **B2B Invoices Section:** Filter sales to registered parties (having valid GSTIN).
    - **B2C Large Section:** Inter-state sales to unregistered parties above the current threshold. ⚖️ I believe this was lowered from ₹2.5 lakh to **₹1 lakh** for periods from Aug 2024 — verify against the current GSTR-1 instructions and keep the threshold in one constant.
    - **B2C Small Section:** Other sales to unregistered parties.
    - **HSN Summary Section:** Group sales quantity, taxable value, and tax breakdown by HSN Code. **Reuse the HSN/SAC helpers from `apps.core.constants`.**
    - **Credit/debit note tables** (from 2.4) and **input-tax summary** (`itc_eligible` lines from 2.2). Read from line snapshots only.
  - [ ] Build export handlers generating **Excel (.xlsx)** or **CSV** files formatted to match standard GST Portal filing requirements.
  - [ ] Build UI view displaying GST summary report cards with "Download GSTR-1 Data" action buttons.

### 4.3 Quotations & Estimates
- [ ] **4.3.1 Estimate Management Schema & Conversion Action**
  - [ ] Build `Quotation` Model and `QuotationItem` Model.
  - [ ] Create API endpoint (`POST /api/v1/quotations/{id}/convert-to-invoice/`):
    - Atomically creates a new **`DRAFT`** `Invoice` using quotation details (the user reviews, then issues).
    - Updates quotation status to `CONVERTED`.
  - [ ] Build UI form for creating Estimates/Quotations and converting them to active GST Invoices with one click.

### 4.4 Mobile API Readiness (For Future Flutter App)
- [ ] **4.4.1 OpenAPI & Mobile Security Standards**
  - [ ] Integrate `drf-spectacular` or `drf-yasg` to generate complete Swagger/OpenAPI documentation (`/api/schema/swagger-ui/`).
  - [ ] Verify JWT token expiration and refresh token workflow (`/api/v1/token/`, `/api/v1/token/refresh/`).
  - [ ] Verify CORS headers allow incoming requests from mobile origin domains or native HTTP clients.
  - [ ] Audit all API endpoints to guarantee strict JSON payloads with consistent error response formats:
    ```json
    {
      "success": false,
      "error": "INSUFFICIENT_STOCK",
      "message": "Only 3 units of Item X remain in stock."
    }
    ```

---

## Phase 5: Testing, Hardening & Production Deployment

### 5.1 Security Auditing & Multi-Tenant Partition Test
- [ ] Write unit test suite testing database multi-tenancy: Verify User A cannot fetch, edit, or view invoices/parties belonging to User B.
- [ ] Verify permission classes (`IsAuthenticated`) on all DRF views.
- [ ] Confirm sensitive environment variables (`SECRET_KEY`, `DATABASE_URL`, `CLOUDINARY_URL`) are loaded from environment variables and not hardcoded.
- [ ] Set `DEBUG = False` in production settings configuration.
- [ ] Schedule `audit_invoices` and `reconcile_stock` weekly (Render cron); a non-zero exit alerts you.

### 5.2 Performance & Database Optimization
- [ ] Add database indexes for high-frequency query paths:
  - `Invoice(business, invoice_date)` — already created in 1.4.3; verify with `EXPLAIN` on production-size data.
  - `Party(business, party_type)`
  - `Item(business, name)` — field is `name`, not `item_name`; already indexed in 1.3.1.
- [ ] Audit Django ORM queries using `django-debug-toolbar` to fix N+1 query problems by applying `select_related()` and `prefetch_related()` on all API views.

### 5.3 Cloud Launch Checklist
- [ ] **Backups & recovery (before any real customer):** Supabase free tier has no point-in-time recovery and pauses inactive projects. Schedule automated `pg_dump` to storage outside Supabase, and perform one real **restore drill**. Issued invoices are legal records.
- [ ] Decide on Render free-tier cold starts (30–60 s after idle) — they hit the live `/preview/` calls hardest; use a paid instance or a keep-alive before launch.
- [ ] Run final production database migrations on Supabase.
- [ ] Execute `collectstatic` to upload static assets to production storage.
- [ ] Deploy updated backend build to Render.
- [ ] Deploy static frontend build to Vercel.
- [ ] Perform full end-to-end smoke test on production environment:
  1. Register new account.
  2. Create Business Profile & Upload Logo.
  3. Add Customer with GSTIN.
  4. Add Inventory Item.
  5. Generate Sales Invoice & Download PDF.
  6. Verify stock auto-deduction.
  7. Log Payment Received & check Party Ledger update.