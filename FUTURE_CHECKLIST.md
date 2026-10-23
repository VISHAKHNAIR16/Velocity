# FUTURE_CHECKLIST.md: Improvements for After the Core Software Works

A living list. Add to it as we build. Work through it only after the main phases in `tasks.md` are done and tested.

Legend: [ ] not started, [x] done

---

## ✅ COMPLETED — Phase 4 (2026-10-22)

The following Phase 4 features are fully built, tested, and merged into `tasks.md`:

- [x] **Purchases module** — Purchase bills, purchase items, purchase returns, stock movements (`PURCHASE` / `PURCHASE_RETURN`), input-tax credit tracking.
- [x] **Expense Management** — Expense categories, operational expenses, summary by category/month, receipt URLs.
- [x] **Quotations & Estimates** — Quote creation, atomic convert-to-invoice (DRAFT invoice + CONVERTED quotation).
- [x] **GSTR-1 & GSTR-3B Reports** — B2B / B2C Large / B2C Small / HSN summary, input-tax credit, CSV + Excel export.
- [x] **All 19 backend tests passing**, lint clean, migrations created, `manage.py check` green.

---

## 1. Security

- [ ] **Login/register rate limiting.** ~~Left out on purpose~~ **Partly done in 2.0.1; the root problem is still open.** All three unauthenticated auth endpoints now share a 10/minute `auth` scope (register and refresh previously had **no** scope at all and fell through to `anon: 60/hour`). What is still missing is `NUM_PROXIES`, so behind Render's proxy every anonymous caller still lands in one IP bucket and one attacker locks out everyone. Fix: configure `NUM_PROXIES` correctly (verify the real `X-Forwarded-For` header on Render from a staging request — a wrong value is silent), or throttle per email address. Tracked as 2.0.5 / decision 24.
- [ ] **Email verification** on registration (stops fake or mistyped emails).
- [ ] **Forgot / reset password** flow by email.
- [x] **JWT blacklist on logout** (`rest_framework_simplejwt.token_blacklist`) so stolen refresh tokens can be revoked. **Done backend-side in 2.0.1 (decision 41):** the app is installed, `BLACKLIST_AFTER_ROTATION = True`, and `POST /api/v1/auth/logout/` blacklists the refresh token, alongside `GET /auth/sessions/`, `DELETE /auth/sessions/{id}/` and `POST /auth/sessions/revoke-others/`.
  - ⚠️ **The frontend does not call any of it yet.** Until a logout button calls `/auth/logout/`, logout from the browser still only deletes its local copy, so from a user's point of view this is unfinished. **Do not tick the user-facing item off on the strength of the API alone.**
  - Note the honest limit: **access tokens cannot be revoked** (stateless), so a revoked session can survive for up to one access-token lifetime. The UI must not promise an instant cut-off.
  - Sessions that predate the migration have no row, so they cannot be listed or revoked. Acceptable pre-launch; the sweep would be blacklisting every `OutstandingToken` for the user.
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
- [ ] GSTR-3B summary and HSN-wise summary. **GSTR-3B is built in Phase 4; HSN-wise summary is done.**
- [ ] Multiple bank accounts and UPI QR code on invoices.
- [ ] Invoice templates and themes.
- [ ] Recurring invoices and payment reminders (WhatsApp/SMS/email).
- [ ] Barcode scanning for items.
- [ ] Hindi and regional language support.

## 4. Quality and Operations

- [ ] Automated tests: especially multi-tenant isolation (User A must never see User B's data), GST calculations, and stock changes.
- [x] **GitHub Actions CI: run `ruff` and tests on each push.** **Done in 2.0.1 (decision 27).** Five jobs: `lint + SQLite suite`, `PostgreSQL concurrency`, `Contract smoke (real HTTP)`, `check --deploy`, `pip-audit`.
  - ⚠️ **Branch protection is still NOT configured.** Until the five jobs are made *required* checks on `main`, red does not block a merge, and "CI is green" remains a habit rather than a gate. This is the single most important remaining CI item.
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

## 6. Mobile (Flutter, Phase 6+)

- [ ] Reuse the same API; confirm token refresh and the error format work in the app.
- [ ] Store tokens in secure storage (Keychain / Keystore), not plain preferences.

## 7. Media and Uploads

- [ ] Serve resized logos (Cloudinary transformations / thumbnails) on invoices and lists instead of the full-size original.
- [ ] Clean up orphaned Cloudinary files. Deleting a user or business deletes the database row but not the stored image.
- [ ] Use separate Cloudinary folders or accounts for dev and production, so test uploads don't mix with real ones.
- [ ] Enforce the upload size limit at the proxy/server level too. Right now the 2 MB limit is checked after the upload has been received.
- [ ] Re-check Cloudinary's current free-tier storage and bandwidth limits before launch.
- [ ] Add a brand-coloured placeholder logo (initials of the business) when no logo is uploaded.

## 8. Multi-Tenancy and Testing

- [x] **Run the test suite against PostgreSQL in CI.** Local tests use in-memory SQLite for speed, so Postgres-specific behaviour is not covered. **Done in 2.0.1:** a `postgres:16` service container runs the concurrency classes. Verified locally against Neon PostgreSQL — 431 tests, 0 skipped.
- [ ] Add PostgreSQL Row-Level Security as a second layer of tenant isolation (defence in depth if application code ever forgets a filter).
- [ ] Add query-count assertions (`assertNumQueries`) to list endpoints to catch N+1 problems early.
- [ ] Tenant data export and deletion tool (full account closure, with Cloudinary file cleanup).
- [ ] Support users belonging to more than one business (the tenant lookup is one function, `get_business()`, so this is a contained change).
- [ ] Admin: a safe "view as business" support mode with an audit trail, instead of raw cross-tenant admin access.

## 9. Phase 6+ Vision

These are features beyond Phase 5:

- [ ] **E-invoicing (IRN / QR code)** and e-way bill generation.
- [ ] **GSTR-2A / 2B auto-reconciliation** with vendor GSTIN matching.
- [ ] **Recurring invoices** and payment reminders (WhatsApp / SMS / email).
- [ ] **Barcode scanning** for items at the billing counter.
- [ ] **Batch / expiry tracking** for perishable inventory.
- [ ] **Multiple godowns / warehouses** per business.
- [ ] **Hindi and regional language support** for the UI.
- [ ] **Unit conversion** (buy in dozens, sell in pieces) — needs a real conversion model.

---

## Removed / Historical

**Purchases module (2.2):** Was removed on 2026-10-12, then **rebuilt from scratch in Phase 4** (2026-10-22). The database tables `purchases_purchase` and `purchases_purchase_item` still exist; a restore is `git revert` plus migrations with no data loss. The Phase 4 build is the current, preferred implementation.

