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

## Phase 1: Core Foundation & GST Invoicing Engine — **IN PROGRESS** (1.1–1.3 verified ✅ · 1.4 plan reviewed & locked — start at 1.4.1)

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
| **1.4.5** | E | Frontend: invoice list + billing form | — | **done** — 380 backend tests + 169 contract checks green |

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
| 17 | 🆕 **Backdating** | Allowed. FY derives from `invoice_date`. UI warns when earlier than the latest issued invoice's date. | Shops enter yesterday's bills; stricter locking can be a later setting. |

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
| 1 | Is HSN/SAC mandatory on a B2B invoice only above ₹5,000, or whenever the recipient has a GSTIN? | Unconditional gate (stricter). `HSN_REQUIRED`. |
| 2 | Confirm the current GST slab structure and that `12%` / `28%` are retired for new invoices (tobacco excepted). | `40` added; 12/28 kept for historical invoices. |
| 3 | Is `0%` being used for both nil-rated and exempt acceptable until we split them? GSTR-1 reports them separately. | Single `0.00` with a code comment. |
| 4 | Confirm the CGST+UTGST list is exactly the five UTs without a legislature. | `{"04","26","31","35","38"}`. |
| 5 | Should round-off be permitted at all for this user's customers/auditors? | Off by default, per business, with the warning text. |
| 6 | Confirm `place of supply` for services uses the recipient's location (s.10(1)(c)/s.12) and that our override hint covers the s.10(1)(b) buyer-directed-delivery case. | Billing state by default + manual override. |
| 7 | Is a data-migration-created `Walk-in / Cash Customer` acceptable, or should cash sales bypass `Party` entirely? | One walk-in party per business. |
| 8 | Compensation cess on luxury goods — out of scope here; confirm no 1.4 invoice needs a cess column. | Not modelled. |

---

## Phase 2: Stock Automation, Purchases & PDF Engine

### 2.1 Automated Inventory Deduction & Tracking
- [ ] **2.1.1 Inventory Trigger System**
  - [ ] Hook into the **`issue`** action (NOT draft creation): inside the same `@transaction.atomic` block that allocates the invoice number, `select_for_update()` the stock rows and decrease them. Drafts never touch stock.
  - [ ] Insufficient stock safety check: Throw validation error if sold stock exceeds current quantity (unless negative stock overrides are allowed). **Gate on the line's snapshot `item_type == PRODUCT` and a non-null `item` — services and free-text lines have no stock and must never trip this check or be deducted.**
  - [ ] Invoice cancellation handler: Restore item stock quantities when an **issued** invoice is cancelled (same transaction as the cancel).

### 2.2 Purchase Management Module
- [ ] **2.2.1 Purchases Schema & API**
  - [ ] Build `PurchaseBill` Model and `PurchaseBillItem` Model (similar structure to Invoice).
  - [ ] Create API endpoints (`POST /api/v1/purchases/`, `GET /api/v1/purchases/`).
  - [ ] Automate Stock Addition: Automatically increase `Item.current_stock` when a purchase entry is saved.
  - [ ] Build Purchase UI form to enter vendor bills, upload supplier bill copies, and update inventory stock counts.

### 2.3 PDF Generation Engine & Document Sharing
- [ ] **2.3.1 PDF Layout & Rendering**
  - [ ] Install PDF rendering library (`WeasyPrint`, `ReportLab`, or `pdfkit`).
  - [ ] Design HTML template (`templates/invoices/tax_invoice.html`) matching standard GST layout:
    - Header: Business Logo, Name, Address, GSTIN, Contact Info.
    - Customer Details: Name, Billing/Shipping Address, GSTIN, State Code.
    - Invoice Meta: Invoice #, Date, Due Date, Place of Supply.
    - Table: Sl No, Item Description, HSN Code, Qty, Unit, Rate, Discount, Taxable Value, CGST Rate/Amt, SGST Rate/Amt, IGST Rate/Amt, Total.
    - Summary Box: Subtotal, Total Tax, Round Off, Grand Total (In Figures and In Words).
    - Footer: Bank Account Details, Terms & Conditions, Authorized Signatory.
  - [ ] Print the constant line **"Tax payable on reverse charge: No"** (Rule 46 requires the declaration; the reverse-charge field itself is deferred — decision 14 in 1.4).
  - [ ] Implement PDF generator service producing binary PDF file buffers.

- [ ] **2.3.2 Cloud Storage & Sharing Integration**
  - [ ] Create background/sync utility to render PDF, save to Cloudinary, and store `pdf_file_url` on the `Invoice` instance. Use **authenticated/private** delivery with expiring signed URLs — invoices contain customer names, GSTINs and addresses, so a permanent public link (e.g. shared over WhatsApp) is a privacy leak.
  - [ ] API endpoint (`GET /api/v1/invoices/{id}/pdf/`) to stream or download PDF directly.
  - [ ] Implement Frontend "Download PDF" and "Print Invoice" buttons.
  - [ ] Implement "Share on WhatsApp" action button generating `https://wa.me/?text=...` link with bill details and invoice URL.

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
  - [ ] Payment Ledger Logic: Automatically recalculate linked Invoice `paid_amount` and `payment_status` (`UNPAID` -> `PARTIAL` -> `PAID`).
  - [ ] Build UI form to record payments received/made with automatic status indicators on invoice lists.

### 3.2 Party Statements & Ledgers
- [ ] **3.2.1 Ledger Computation Engine**
  - [ ] Implement Party Ledger Service (`services/ledger_engine.py`):
    - Retrieve all Sales Invoices, Purchase Bills, Payments In, and Payments Out for a specific party in chronological order.
    - Compute running account balances: Balance = Opening Balance + Invoices - Payments Received.
    - Use `Party.opening_balance_signed` (a `Decimal`) — never convert to `float`.
  - [ ] API Endpoint (`GET /api/v1/parties/{id}/statement/?start_date=...&end_date=...`).
  - [ ] Build UI Statement View: Displays statement table with filtering by date range, total billed amount, total paid, pending balance, and export to PDF.

### 3.3 Business Analytics Dashboard
- [ ] **3.3.1 Summary Aggregations API**
  - [ ] Implement `/api/v1/dashboard/stats/` API endpoint returning aggregated statistics:
    - **Total Sales Amount** (Current month vs overall).
    - **Total Purchase Amount**.
    - **Total Receivables** (Money owed by customers).
    - **Total Payables** (Money owed to suppliers).
    - **Total Net Profit** (Sales - Purchases - Expenses).
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