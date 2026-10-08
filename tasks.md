# TASKS.md — Master Project Tracker

This document tracks the step-by-step implementation of the GST Billing, Inventory, and Accounting System. Update task checkboxes as features are completed to maintain a clear state of progress.

---

## Task Management Rules for AI & Developers
1. **Never skip a phase:** Complete database models, API views, validation logic, and frontend UI tests before marking a parent task as completed.
2. **Multi-Tenancy Check:** Ensure every newly created model includes a FK to `User` / `BusinessProfile` and every query filters by `user=request.user`.
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

## Phase 1: Core Foundation & GST Invoicing Engine — **IN PROGRESS** (1.1–1.3 verified ✅ · 1.4 next)

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
Every policy below was verified with exact `Decimal` arithmetic before being written down.

---

#### 1.4.0 Locked decisions (do not re-litigate these mid-build)

| # | Decision | Rule | Verified |
|---|---|---|---|
| 1 | **Rounding** | Per line: taxable value → 2dp `ROUND_HALF_UP`; tax per line → 2dp; invoice total = **exact sum of line totals** | Rounding only the grand total diverges by 1 paisa (`10.55@18%`→1.90 + `13.13@12%`→1.58 = 3.48 vs grand-only 3.47). Printed lines would not add up. |
| 2 | **Intra-state split** | `cgst = q2(rate/2)`, `sgst = q2(rate − cgst)` — the odd paisa goes to SGST so they always re-add | All 8 slabs reconcile: `0.25%` → `0.13 + 0.12 = 0.25`. Splitting 0.25% naively gives 0.125, which is not representable. |
| 3 | **Discounts** | Line-level **and** invoice-level; invoice-level apportioned pro-rata by line value; tax charged on the **discounted** value | Apportioned shares sum exactly to the total discount (₹100.00 + ₹50.00 = ₹150.00). |
| 4 | **Place of supply** | Party **shipping** state, falling back to billing state | GST follows where goods are delivered, not where the bill is addressed. |
| 5 | **Tax-inclusive prices** | When flagged, **extract** tax: `taxable = price / (1 + rate/100)`. Refuse to mix inclusive and exclusive lines on one invoice | Getting it backwards over-charges **₹21.24** on a ₹118 item at 18%. |
| 6 | **Lifecycle** | `DRAFT` (editable) → `ISSUED` (frozen permanently) → `CANCELLED` (reason + audit). Credit notes deferred. | A typo must not mean re-entering an invoice. |
| 7 | **Round off** | **Optional per business**, default OFF — see 1.4.5. | Rounding is the business's choice, not ours. |

---

#### 1.4.1 Foundations

- [ ] **`apps/core/money.py`** — the single owner of money rules, so the API, the JS preview and the
      PDF can never disagree. Pure functions, `Decimal` only, no DB, no request:
  - `q2(value)` / `q3(value)` — quantise to 2 / 3 dp with `ROUND_HALF_UP`.
  - `split_intra_state(rate)` → `(cgst, sgst)` that always re-adds to `rate`.
  - `apportion_pro_rata(total, weights)` → parts that sum **exactly** to `total`.
  - `amount_in_words(amount)` → `"One Lakh Twenty Three Thousand Four Hundred Fifty Six and Seventy Paise Only"`
      (required on every tax invoice; built here so the PDF in 2.3 just calls it).
- [ ] **`apps/core/fiscal.py`** — Indian financial year helpers (FY runs **1 April – 31 March**, not the
      calendar year): `financial_year(date) -> "2026-27"`, `fy_bounds(label)`.
- [ ] **New app `apps/invoices/`** — `models.py`, `serializers.py`, `views.py`, `urls.py`, `tests.py`,
      `admin.py`, `services/gst_calculator.py`, `services/numbering.py`.
- [ ] **Use the existing `TenantPrimaryKeyRelatedField`** (`apps/core/fields.py`) for `Invoice.party`
      and `InvoiceItem.item`. It has been written since 1.1 but **never used**; it fails closed, so a
      cross-tenant ID becomes a 400 instead of a data leak.
- [ ] Re-export nothing new from `apps/core/constants.py` — add `apps/accounts/constants.py` style
      single sources only if a second list is genuinely needed.

---

#### 1.4.2 `BusinessProfile` additions (migration on `accounts`)

- [ ] `round_invoice_total` `BooleanField(default=False)` — "Round invoice total to the nearest rupee".
      **Default OFF.** See 1.4.5.
- [ ] `invoice_number_prefix` `CharField(default="INV", max_length=10)`.
- [ ] `invoice_number_next` `PositiveIntegerField(default=1)` — used with `InvoiceCounter` for locking.
- [ ] Expose all three on `BusinessProfileSerializer` + the business profile UI
      (a small "Invoice preferences" section).
- [ ] **Write the user-facing caveat verbatim next to the toggle** (see 1.4.5).
- [ ] Run migration + backfill existing businesses to the defaults.

---

#### 1.4.3 Data schema

- [ ] **`Invoice`** model (inherits `TenantModel`)
  - `party` FK → `PROTECT` (an invoice's customer must never be deleted out from under it).
  - `invoice_number` `CharField` — allocated on **issue**, unique per business
      (`UniqueConstraint(business, invoice_number)`).
  - `invoice_date` and `due_date` as **`DateField`** (no time component — an invoice dated 7 Oct must
      not slip to 6 Oct through UTC conversion).
  - `place_of_supply` `CharField(max_length=2)`, `supply_type` (`INTRA` / `INTER`).
  - `status` (`DRAFT` / `ISSUED` / `CANCELLED`), `issued_at`, `cancelled_at`, `cancellation_reason`.
  - **Snapshots so history can never be rewritten:** `recipient_name`, `recipient_gstin`,
      `recipient_address`, `business_gstin`, `business_state_code`, `document_title`.
  - `document_title` = `Tax Invoice` only when the business is GST-registered, else `Bill of Supply`
      (printing "Tax Invoice" without a GSTIN is wrong).
  - Totals: `subtotal`, `total_discount`, `taxable_total`, `cgst_total`, `sgst_total`, `igst_total`,
      `round_off` (`default=Decimal("0.00")`), `grand_total`.
  - **Indexes from day one** — this will be the largest table we ever have:
      `(business, invoice_date)`, `(business, status)`, `(business, party)`.
  - **Deferred to 3.1, deliberately:** `paid_amount`, `balance_due`, `payment_status`. Adding them
      later is one migration; adding them now with guessed semantics is worse.
- [ ] **`InvoiceItem`** model
  - `invoice` FK (`CASCADE` — deleting a draft removes its lines), `item` FK → **`PROTECT`**, nullable
      (an item deleted later keeps the line, which still has its snapshot).
  - Snapshots: `item_name`, `hsn_sac_code`, `unit`, `service_description`, `price_includes_tax`.
  - Money: `quantity` `Decimal(12,3)`, `unit_price` `Decimal(12,2)`, `line_discount` `Decimal(12,2)`,
      `invoice_discount_share` `Decimal(12,2)`, `taxable_value`, `tax_rate` `Decimal(5,2)`,
      `cgst_amount`, `sgst_amount`, `igst_amount`, `total_amount` (all `Decimal(12,2)`).
- [ ] **`InvoiceCounter`** model — `business`, `financial_year`, `last_number`,
      `UniqueConstraint(business, financial_year)`.
- [ ] Run migrations; `makemigrations --check` clean.

---

#### 1.4.4 GST calculator (`services/gst_calculator.py`)

Pure functions, no DB, no request — that is what makes them exhaustively testable in milliseconds.

- [ ] `line_taxable_value(quantity, unit_price, line_discount, invoice_share, includes_tax, rate)`
- [ ] `calculate_invoice(lines, business, party, invoice_discount, round_invoice_total)` → totals dict
- [ ] **Order of operations per line** (getting this order wrong is the usual bug):
  1. `gross = q2(quantity × unit_price)`
  2. less `line_discount`
  3. less apportioned `invoice_discount_share`
  4. if `includes_tax`: `taxable = q2(net / (1 + rate/100))` and tax is **extracted**
      else: `taxable = net` and tax is **added on top**
  5. `tax = q2(taxable × rate/100)`; intra → `split_intra_state(rate)`, inter → `igst` only
  6. `total = taxable + tax`
- [ ] **Supply type:** `business.state_code` vs party shipping state (fallback billing state).
      Compare against `apps.core.constants.GST_STATE_CHOICES`.
- [ ] Reuse `GST_RATE_CHOICES`, `is_hsn`, `is_sac` from `apps/core/constants.py` — no new rate list.
- [ ] Reject (never silently coerce): zero/negative quantity, negative price, mixed
      inclusive/exclusive lines, an empty line list, a rate outside `GST_RATE_CHOICES`.
- [ ] **`INV_STOCK` gate:** never deduct stock for a `SERVICE` — call `item.tracks_stock`. Stock maths
      itself lands in **2.1**; 1.4 only must not create the wrong seam for it (see 1.4.10).

---

#### 1.4.5 Round off — optional per business, and the GSTR-1 caveat

- [ ] **Default OFF.** With it off, `round_off` is always `Decimal("0.00")` and the grand total is the
      exact sum of line totals.
- [ ] **When ON:** `sum_of_line_totals = Σ line.total_amount`, then
      `grand_total = q2_to_nearest_rupee(sum_of_line_totals)` and
      `round_off = grand_total − sum_of_line_totals` (may be `+0.50` or `-0.50`).
- [ ] **`round_off` is presentational only.** Taxable values and tax amounts are computed on the
      per-line values and are **never** affected by it.
- [ ] **Ship this warning next to the UI toggle, word for word:**

  > Rounding changes only the amount payable on this invoice. It does **not** change any taxable
  > value or tax amount. When you file GSTR-1 or GSTR-3B, report the **taxable value and tax** shown
  > against each HSN/SAC code — never include the round-off as taxable value, and never treat it as a
  > discount. Your HSN-wise summary must stay on the pre-rounding figures. If your accounts are
  > audited, consider leaving rounding off so every invoice total equals the sum of its lines exactly.

- [ ] Show a `Round Off` row on the invoice/PDF **only** when it is non-zero, so an off-by-a-paisa
      surprise never appears on an invoice that did not ask for one.
- [ ] Frontend: a live note under the toggle and a small banner on the billing screen while it is on.

---

#### 1.4.6 Lifecycle, numbering and integrity

- [ ] **`DRAFT`** — created with lines, fully editable, deletable. Shows as `Draft #<pk>` with **no  
      invoice number yet**, so abandoned drafts never leave gaps in the legal series.
- [ ] **`ISSUED`** — `POST /invoices/{id}/issue/`:
  - Re-validate every line, re-run the calculator server-side (never trust the client's numbers).
  - Allocate the number under `select_for_update()` on `InvoiceCounter` **inside the same
    `@transaction.atomic` block**, honouring `invoice_number_prefix` and the Indian FY
    (`INV/2026-27/0001`, resetting on 1 April).
  - Snapshot party + business details, freeze the record forever.
- [ ] **`CANCELLED`** — `POST /invoices/{id}/cancel/` requires a reason, stamps `cancelled_at`, and
      **never frees the number for re-use**. A cancelled number must stay cancelled forever.
- [ ] Editing or deleting an `ISSUED` invoice → `400` with a clear message, never a silent success.
- [ ] **Double-submit protection:** the issue endpoint must be idempotent per invoice id, and the
      frontend disables the button. A double-click must never burn two invoice numbers.
- [ ] **Business state gate (carried from 1.2 decision #1):** invoice creation returns
      `400 BUSINESS_PROFILE_INCOMPLETE` when `BusinessProfile.state_code` is blank. Without it a blank
      state reads as "different from the party", so every sale becomes IGST instead of CGST+SGST —
      same grand total, wrong return. Add a **UI warning before submit**, not only at the error.
- [ ] Out-of-stock is **not** checked in 1.4 (that is 2.1) — but the `issue` transaction must be
      structured so 2.1's deduction slots in without restructuring invoices.

---

#### 1.4.7 API

- [ ] `InvoiceViewSet(TenantModelViewSet)` at `/api/v1/invoices/`
  - `GET /invoices/` — filters: `status`, `party`, `date_from`, `date_to`, `search` (number / party name).
  - `POST /invoices/` — create a `DRAFT` with nested lines.
  - `GET/PATCH/DELETE /invoices/{id}/` — `PATCH`/`DELETE` only while `DRAFT`.
  - `POST /invoices/{id}/issue/`, `POST /invoices/{id}/cancel/`.
  - `POST /invoices/preview/` — recalculate totals **without saving**.
- [ ] **`/preview/` is the important one:** the JavaScript calls the server instead of reimplementing
      GST in the browser, so the preview can never drift from the saved invoice and there is exactly
      **one** source of truth for tax arithmetic.
- [ ] `InvoiceSerializer` with nested line create/update, plus a light `InvoiceListSerializer`.
- [ ] Use `TenantPrimaryKeyRelatedField` for `party` and every line's `item`.
- [ ] Wrap create and issue in `@transaction.atomic`.
- [ ] **HSN/SAC-wise tax summary endpoint/field** — required on a tax invoice to a registered
      recipient above ₹5,000, and reused by GSTR-1 in 4.2. Derive it from line data while it exists.

---

#### 1.4.8 Frontend: billing engine

- [ ] `Web_Frontend/invoices/index.html` — invoice list, same visual language as parties/items.
- [ ] `Web_Frontend/invoices/billing.html` — the billing form:
  - [ ] Party picker (search, shows address + GSTIN + state).
  - [ ] Dynamic line rows: item search, qty, unit, rate, discount, tax rate, live line total.
  - [ ] Invoice-level discount with live apportioned-per-line preview.
  - [ ] Product/Service aware: a service row hides quantity-vs-stock concerns and shows the SAC + description.
  - [ ] Live totals from `/invoices/preview/` — **never GST maths in JavaScript.**
  - [ ] HSN/SAC-wise tax summary table.
  - [ ] Round-off row shown only when the business has rounding enabled.
  - [ ] Save as draft → Issue → Print.
- [ ] Add "Invoices" to `renderNav` in `shared/js/ui.js`; use the shared `h()` helpers.

---

#### 1.4.9 Testing (~70 tests)

- [ ] **Calculator unit tests** (no DB, no HTTP — pure and fast): all 8 GST slabs × intra/inter ×
      inclusive/exclusive × line/invoice discounts, including the 0.25% and 1.5% odd splits.
- [ ] **Golden tests** — fixed scenarios with exact expected numbers, so any future change that moves a
      total by even one paisa fails loudly.
- [ ] **The invariant test that matters most**, over randomised line sets:
      - `Σ line.total_amount == grand_total` (when round-off is off)
      - `cgst + sgst + igst == Σ line tax`
      - `Σ apportioned discount == invoice discount`
      - `Σ line taxable_value == taxable_total`
- [ ] **Lifecycle tests** — draft editable, issued frozen, cancel needs a reason, number never reused.
- [ ] **Numbering tests** — FY rollover on 1 April, per-business independence, `select_for_update`
      under two concurrent issues (no duplicate, no skipped number).
- [ ] **Tenant isolation** — list/count/search/filters, retrieve/update/delete/cancel of another
      tenant's invoice → 404, cross-tenant `party`/`item` id → 400.
- [ ] **Literal-URL tests** — hit `/api/v1/invoices/` etc. as hardcoded strings, **never `reverse()`**
      (see the 1.3 router bug in `1.3.7`).
- [ ] Rounding-off on/off: `round_off` is `0.00` when off, and taxable values are identical either way.

---

#### 1.4.10 Deliberately out of scope

- [ ] **Stock deduction and the insufficient-stock check** → **2.1** (1.4 must not block it).
- [ ] **PDF generation, bank details, signature, amount-in-words rendering** → **2.3**
      (`amount_in_words()` itself is built in 1.4.1 because the data must exist).
- [ ] **Payments, `paid_amount`, `payment_status`, balance due** → **3.1**.
- [ ] **Credit / debit notes** → later phase (see `FUTURE_CHECKLIST.md`).
- [ ] **E-invoicing (IRN/QR) and e-way bills** → `4.4`; turnover-gated.

---

---

## Phase 2: Stock Automation, Purchases & PDF Engine

### 2.1 Automated Inventory Deduction & Tracking
- [ ] **2.1.1 Inventory Trigger System**
  - [ ] Update `Invoice` creation view: Wrap creation in `db.transaction.atomic()` to guarantee that item stock counts automatically decrease when a sales invoice is created.
  - [ ] Insufficient stock safety check: Throw validation error if sold stock exceeds current quantity (unless negative stock overrides are allowed). **Gate on `item.tracks_stock` — services have no stock and must never trip this check.**
  - [ ] Invoice cancellation handler: Restore item stock quantities if an invoice is voided or cancelled.

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
  - [ ] Implement PDF generator service producing binary PDF file buffers.

- [ ] **2.3.2 Cloud Storage & Sharing Integration**
  - [ ] Create background/sync utility to render PDF, save to Cloudinary, and store `pdf_file_url` on the `Invoice` instance.
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
  - [ ] Implement `POST /api/v1/payments/` API endpoint.
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
    - **B2C Large Section:** Inter-state sales > ₹2.5 Lakhs to unregistered parties.
    - **B2C Small Section:** Other sales to unregistered parties.
    - **HSN Summary Section:** Group sales quantity, taxable value, and tax breakdown by HSN Code. **Reuse the HSN/SAC helpers from `apps.core.constants`.**
  - [ ] Build export handlers generating **Excel (.xlsx)** or **CSV** files formatted to match standard GST Portal filing requirements.
  - [ ] Build UI view displaying GST summary report cards with "Download GSTR-1 Data" action buttons.

### 4.3 Quotations & Estimates
- [ ] **4.3.1 Estimate Management Schema & Conversion Action**
  - [ ] Build `Quotation` Model and `QuotationItem` Model.
  - [ ] Create API endpoint (`POST /api/v1/quotations/{id}/convert-to-invoice/`):
    - Atomically creates a new `Invoice` using quotation details.
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
  - `Invoice(business, invoice_date)`
  - `Party(business, party_type)`
  - `Item(business, item_name)`
- [ ] Audit Django ORM queries using `django-debug-toolbar` to fix N+1 query problems by applying `select_related()` and `prefetch_related()` on all API views.

### 5.3 Cloud Launch Checklist
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
