# FUTURE_CHECKLIST.md: Improvements for After the Core Software Works

A living list. Add to it as we build. Work through it only after the main phases in `tasks.md` are done and tested.

Legend: [ ] not started, [x] done

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
- [ ] GSTR-3B summary and HSN-wise summary.
- [ ] Multiple bank accounts and UPI QR code on invoices.
- [ ] Invoice templates and themes.
- [ ] Recurring invoices and payment reminders (WhatsApp/SMS/email).
- [ ] Barcode scanning for items.
- [ ] Hindi and regional language support.

## 4. Quality and Operations

- [ ] Automated tests: especially multi-tenant isolation (User A must never see User B's data), GST calculations, and stock changes.
- [x] **GitHub Actions CI: run `ruff` and tests on each push.** **Done in 2.0.1 (decision 27).** Five jobs: `lint + SQLite suite`, `PostgreSQL concurrency`, `Contract smoke (real HTTP)`, `check --deploy`, `pip-audit`.
      - ⚠️ **Branch protection is still NOT configured.** Until the five jobs are made *required* checks on `main`, red does not block a merge, and "CI is green" remains a habit rather than a gate. This is the single most important remaining CI item.
      - **Do not delete `defaults.run.working-directory: Backend`.** Every Python file lives under `Backend/` (`pyproject.toml`, `uv.lock`, `requirements.txt`, `manage.py`); none exist at the repo root. Removing that block makes all five jobs fail in ~30 seconds with `No pyproject.toml found in current directory or any parent directory`. See §12.
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

- [x] **Run the test suite against PostgreSQL in CI.** Local tests use in-memory SQLite for speed, so Postgres-specific behaviour is not covered. **Done in 2.0.1:** a `postgres:16` service container runs the concurrency classes, which removes the Docker/WSL and remote-Neon prerequisites from the developer machine entirely. Verified locally against Neon PostgreSQL — 431 tests, 0 skipped.
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
- [x] **Turnover-based HSN digit minimum.** ~~The `HSN_REQUIRED` gate is unconditional, which is stricter than the statutory ₹5,000-per-invoice B2B threshold. Make the threshold a business setting.~~ **Done in 2.0.3 (decision 22).**
      ⚠️ **This entry used to state the law wrongly and must not be revived.** It described a "statutory ₹5,000-per-invoice B2B threshold" as the rule. There is no per-invoice ₹5,000 HSN rule — ₹5,000 is an old reverse-charge daily limit. The digit count required depends on the **business's own annual turnover** (4 digits up to ₹5 crore, 6 above), not on an invoice's value or its recipient. Decision 22 therefore **removed** the `STATUTORY` mode entirely rather than making it configurable: `BusinessProfile.hsn_min_digits` is `4` (default) or `6`, and every line of every tax invoice needs a code of at least that length.
      - Both readings are ⚖️-marked and **still need a CA to confirm** (tasks.md 1.4.8 question 1). The default of `4` is the permissive one, chosen because a superfluous digit is recoverable and a missing one is not.
      - Still worth doing: a **UI affordance** that only *warns* about a too-short code before issue, since the server currently refuses at issue time.
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
## 12. CI hardening (added after the first GitHub Actions run failed)

Everything here came out of debugging run #1, which failed in **33 seconds**.

- [ ] **Set branch protection.** Make all five jobs required checks on `main`. Until this is done,
      red does not block anything and the whole workflow is advisory.
- [ ] **Exercise the mutation guard with one throwaway PR.** The guard only runs
      `if: github.event_name == 'pull_request'`, so it is *proven locally but never yet proven in
      CI*. A PR whose only purpose is to trip it is the cheapest possible check that the Postgres
      job can go red end to end.
- [x] **Record the accepted `check --deploy` warning instead of silencing it.** `security.W021`
      (`SECURE_HSTS_PRELOAD`) is accepted deliberately: HSTS is on (3600 s, include-subdomains),
      and preload additionally submits the domain to the *browser* preload list, which cannot be
      withdrawn later without users taking manual action. Revisit once the production domain is
      stable. The accepted list is written into the workflow's `$GITHUB_STEP_SUMMARY` on every run,
      so a **new** warning forces someone to edit a file rather than quietly suppress a check.
- [ ] **Raise `SECURE_HSTS_SECONDS` from 1 hour to 1 year** once the domain is stable and verified
      (also listed in §1).
- [ ] **Re-check `pip-audit` cadence.** It is now blocking on runtime dependencies (green as of
      this writing — "No known vulnerabilities found"), with dev dependencies informational.
      Consider a scheduled weekly run in addition to per-push, so a newly published advisory is
      caught even on a quiet repo.
- [x] **The stock ledger has its own CI coverage and its own mutation guard.** `postgres-concurrency`
      now also runs `apps.inventory.tests_stock_concurrency`, and a second guard job removes
      `select_for_update()` from `apps/inventory/services/stock.py` and requires the suite to go red.
      The first guard only mutated `issue.py`, so it would **not** have caught a broken stock lock.
      Verified locally against Neon: that mutation fails 3 of the 14 stock concurrency tests.
- [x] **Frontend tests run in CI.** `Web_Frontend/tests/` (jsdom + `node:test`) is a step in
      `lint-and-test`, installed with `npm ci` against a committed `package-lock.json`. jsdom is the
      only dependency, kept deliberately small.
- [ ] **Add `ruff format` as a gate, deliberately later.** The existing code was hand-formatted and
      47 files differ from `ruff format`'s defaults, so enabling it today means a repo-wide
      reformat that buries any real diff. `ruff check` is the lint gate; adopting the formatter
      should be its own single commit.
- [x] **The `Backend/` working-directory trap is documented** at the top of `ci.yml` and in
      tasks.md 2.0.1, with the exact per-job failure table. Four of the five jobs die at their
      first real step, so the symptom is fast and the cause is unambiguous — but it cost a full
      red run to diagnose, because every one of those commands works perfectly when run by hand
      from `Backend/`. **A command that works locally proves nothing about where CI runs it.**
- [x] **`SECRET_KEY` / `DEBUG` dummies moved to the workflow-level `env:` block** after run #2
      failed with `ImproperlyConfigured`. `settings.py` deliberately refuses to boot without a
      secret when `DEBUG` is false; `Backend/.env` sets `DEBUG=True`, so **locally that failure
      is unreachable** and only CI ever sees it. Second occurrence of the same pattern — *the
      local environment is more forgiving than production*, in the same way SQLite is more
      forgiving than PostgreSQL about row locks.
      - Standing rule for this repo: **anything behind `if not DEBUG:` is untested locally.** Run
        the suite with `DEBUG=False` at least once per phase, not only `check --deploy`.
- [ ] **Nothing reads the CI logs yet.** With no `gh` CLI installed and no log shipping, diagnosing
      run #1 required guessing from the job/step timeline plus reproducing the error locally. It
      worked, but it was slower than it should have been. Options: install `gh`, or enable step
      debug logging (`ACTIONS_STEP_DEBUG`) on failure.
  - This cost real time on **both** runs so far. Installing `gh` is now the highest-value
    operational fix in this file.

---

---

## 6. Phase 2.1 (stock ledger) — follow-ups

Raised while building 2.1.1-2.1.5. None of these block the ledger; they are the
things that will bite later if they are not recorded.

- [ ] **The concurrency tests cannot be run against a pooled connection.** `config/settings_test_pg.py`
      now rewrites a `-pooler` host to its direct equivalent and says so, because PgBouncer in
      transaction-pooling mode reassigns server connections per transaction, and `SELECT ... FOR UPDATE`
      is only safe while the whole transaction stays on one. It also breaks teardown, since the pooler
      holds idle sessions and `DROP DATABASE` fails. CI uses a plain `postgres:16` service, so it never
      sees this. If you add a test that must not go through a pooler, the rewrite is there.
- [ ] **`STOCK_CONCURRENCY_WORKERS` defaults to 3.** Each worker holds its own connection and the
      direct Neon endpoint has a low concurrency cap — past it the server closes connections outright.
      Raise it when the database can take it; the tests are designed to run on a shared dev branch.
- [ ] **Threaded tests must close their own connections.** Django opens one connection per thread and
      a connection cannot be closed from another thread, so every worker now calls
      `close_thread_connections()`. Without it the suite passes and the command still exits non-zero
      on `database "test_..." is being accessed by other users` — a failure that looks like a flaky
      test and is really untidy teardown.
- [ ] **A race test that cannot lose is not a test.** Verified here: with `select_for_update()` removed
      from `_lock_items`, the behavioural last-unit race test still passed, because threads rarely
      collide inside the few hundred microseconds between the stock check and the write. The
      deterministic guards are `LockIsActuallyRequestedTests` (asserts the SQL really contains
      `FOR UPDATE`) and the CI mutation job that removes the lock and requires the suite to go red.
      Standing rule: **any new concurrency test needs a mutation check before it is trusted.**
- [ ] **Two real bugs were found only by tests, not by review** — worth remembering as the argument
      for writing them:
      1. `adjust_stock` and `set_tracking` called `select_for_update()` outside a transaction. That is a
         500 on PostgreSQL and is *silently ignored* on SQLite. Third occurrence in this repo of the
         same shape: the local environment is more forgiving than production.
      2. The custom exception handler rewrote `response.data` and discarded the `details` array, so
         `INSUFFICIENT_STOCK` reached the frontend with no indication of which item was short.
- [ ] **`reconcile_stock` is the only repair tool, and it repairs only the cache.** The ledger is
      authoritative; `--fix` never writes a fabricated movement to make the numbers agree. If the
      ledger itself is wrong, correct it the way a human would: `Adjust Stock`. Keep that rule — an
      auto-generated "fixing" movement is indistinguishable from a real one six months later.
- [ ] **The ledger is append-only in the strict sense.** Bulk `.update()` and `.delete()` on
      `StockMovement` raise, so a future "optimisation" that trims the table to save space will fail
      loudly rather than quietly destroying the audit trail. Keep it that way; add an explicit
      retention rule if the table ever gets large.
- [ ] **Stock unit conversion is out of scope (decision 37) and will look like a bug to users.**
      Selling in dozens against stock in pieces is neither converted nor checked. It needs a real
      conversion model, not a guess. Flag it in the UI before someone files a bug about it.
- [x] **Purchases (2.2) now write through the same ledger** (`PURCHASE` / `PURCHASE_REVERSAL`, same
      lock order, same idempotency). Until then the only ways stock increases are `OPENING` at item
      creation and `Adjust Stock`, and `reconcile_stock` will show drift the moment a shop's real
      process needs stock-in.
- [ ] **Real-browser sign-off for 2.1.5 is outstanding.** `docs/QA_CHECKLIST.md` section 4 is written
      and unticked. There is no browser or Playwright runner in the dev environment, so the UI was
      verified with 20 jsdom tests instead — enough to catch wording and wiring bugs, **not** enough to
      call it done. See also the 2.0.9 browser pass, which is in the same position.
- [ ] **`gh` CLI still not installed.** Unchanged from the note below, and still the highest-value
      operational fix in this file.

- [x] **The fast test loop is 7x shorter, with no coverage lost.** Measured, not estimated:
      `manage.py test apps` took **63s**, and profiling every module separately showed why —
      `apps.invoices.tests.test_contract` alone took **54s** for 9 tests, while the other 677
      tests took **9s in total**. The cost is `LiveServerTestCase`: each test commits (so the
      database is flushed and fixtures are rebuilt) and a real socket server is handled on its
      own thread with its own connection. A plain `TransactionTestCase` next to it costs 0.028s
      for the same work, so the server thread is the entire difference.

      Two hypotheses were tested and **rejected** before the cause was pinned down: the
      `DatabaseCache` from 2.0.5 (54.2s either way) and `serialized_rollback` (only 16 rows to
      deserialise). Worth recording, because both looked like obvious culprits and a
      plausible-sounding guess is how a performance problem gets "fixed" in the wrong place.

      The class is now tagged `contract`; the full suite runs `--exclude-tag=contract`, and CI's
      dedicated `contract-smoke` job runs `--tag=contract`. **That job already existed**, so the
      contract tests were being run twice per push — once in the full suite and once in the job
      built for them. The fix was removing the duplicate, not the coverage.
- [x] **`dev.ps1` gives the fast loop a name.** `\.\dev.ps1 test` is 677 tests in ~11s against
      65s for `test-all`, and `\.\dev.ps1 help` lists the rest. A Makefile was written first and
      deleted: **`make` is not installed on the developer's machine**, so it would have been a
      file nobody could run.
      - Its `lint` task uses `Start-Process -PassThru` rather than `$LASTEXITCODE`. Reading
        `$LASTEXITCODE` from a native command inside that script returned 0 for a ruff run that had
        just printed "Found 2 errors", so `lint` cheerfully reported success on broken code. A
        lint command that cannot fail is worse than none, and this one had to be verified by
        injecting a real error — not by running it against clean code and seeing green.
- [ ] **The 54s contract tests could still be made faster.** They are not understood in detail
      beyond "`LiveServerTestCase` is expensive"; the per-test cost is ~6s and is not accounted
      for. Not urgent — they run in their own CI job — but a future `contract` tag on a new
      expensive test class would quietly add a minute to every push.

---

## 7. Phase 2.2 (purchases) — follow-ups

- [x] **Amounts are entered, never overwritten.** A purchase bill is transcribed from the supplier's
      document, so the typed figures are the truth and `calculate_invoice()` became a validator. Both
      versions are stored (`*_computed` beside the entered ones) and the gap is recorded in
      `max_divergence`. Verified by mutation: a frontend change that rewrites the typed totals is
      caught by `purchases.test.js`.
- [x] **`preflight_constraints` picked up all 14 new purchase constraints with no edit**, going from
      18 checks to 32. That is the whole argument for deriving the checks from
      `Model._meta.constraints` instead of a hardcoded list — the script this replaced would have
      reported "all clear" for every one of them.
- [ ] ⚠️ **`preflight_constraints --app <name>` was silently checking nothing, and my own filter
      reintroduced it.** It compared `_meta.app_label` against a set built from `_meta.label`
      ("purchases" vs "purchases.Purchase"), so the filter never matched and the command printed
      "all clear" having verified nothing — the exact failure mode it exists to prevent. Now fixed and
      covered, but it is worth a test for *every* filter this command has, not just the happy path.
- [x] **The `origin_state` rename is complete and provably safe.** `calculate_invoice(business_state=)`
      became `origin_state=`; all four call sites moved and the 461-test sales suite was re-run. The
      risk flagged before starting — a keyword rename on a function sales depends on — did not bite.
- [x] **Two `CheckConstraint`s were written inverted**, the same shape 2.0.7 hit twice:
      `Q(status="CANCELLED") | ...` permits the *absence* of data on any status. Both fixed to the
      `~Q(status=...) | ...` form the invoice constraints use. Third occurrence of this bug in the
      project: worth writing the lifecycle constraints from the invoice file rather than from memory.
- [x] **Posting a purchase and posting an invoice are asymmetric under concurrency.** Two bills for
      the same item posted at once must **both** apply — the goods arrived twice, so dropping one
      would lose stock. Cancellation is the dangerous direction and must reverse exactly once. A test
      that assumed "exactly one winner" here would have been wrong in the most expensive direction.
- [ ] **Reverse charge is still deferred to 2.4**, as specified. A purchase from an unregistered or
      composition supplier currently cannot carry reverse-charge tax, so a business with such suppliers
      cannot fully record their input credit.
- [ ] **`itc_eligible` is stored but nothing consumes it.** 4.2 (GSTR-3B) has to total the claimable
      credit from it. Until then it is a field that is written and never read, which is exactly the
      kind of thing that quietly stops matching reality.
- [ ] **Supplier bill attachments remain deferred.** When they are built they must NOT go on the
      existing Cloudinary storage: this project stores media on a public CDN by default, and a supplier
      bill carries bank details and GSTINs.
- [ ] **The purchase page has no browser sign-off.** `docs/QA_CHECKLIST.md` section 6 is written and
      unticked, for the same reason as 2.0.9 and 2.1.5 — no browser runner in this environment.

---

## 8. Phase 2.3 (PDF templates) — follow-ups

- [ ] ⚠️ **The `VCRM` reference does not transfer, and it is worth remembering why.** It deploys to
      **Railway** (`Procfile`, `railway.json`), whose Nixpacks base image ships Pango. WeasyPrint works
      there with no Dockerfile *because the platform image supplies the library invisibly*. Render's
      native Python runtime does not, which is why 2.3 needs a Dockerfile at all. Anyone who "remembers"
      WeasyPrint working on Render is remembering Railway.
- [ ] ⚠️ **A `CLOUDINARY_URL`-less deployment silently loses every logo and signature.** Media falls
      back to local disk (`settings.py:198`), which is ephemeral inside a container. The 2.3 PDF puts
      both on every invoice, so this becomes visible the moment it bites.
- [ ] **WeasyPrint is lazily imported and its render tests are `skipUnless`-guarded.** The guard was
      written on the assumption that the dev machine could not run Pango, and that assumption was wrong
      — a GTK runtime happens to be installed here, so PDFs render locally too. The guard is kept
      anyway: it is cheap, it matches `backend_supports_row_locks()`, and it is the only thing standing
      between this suite and a hard failure on a colleague's fresh Windows clone. **Measure before
      claiming a machine cannot do something.**
- [ ] **Both templates share partials on purpose.** They must be free to differ in styling and
      incapable of differing in figures. A test asserting the two print identical numbers is what keeps
      that true when someone edits one and forgets the other.
- [ ] **The backfill stamps today's bank details onto historical invoices.** Wrong history, chosen
      deliberately over blank boxes. If a business's bank details were wrong on an old invoice, that
      invoice now shows the corrected account, and no amount of auditing would have caught it.
- [ ] **Template choice is per download and stored nowhere.** The trade-off is deliberate: a reprint
      can look different from the copy the customer received. If that ever matters (a disputed invoice),
      it becomes a snapshotted field plus a migration — noted here so the omission is a decision
      rather than an oversight.

---

## 9. Phase 2.3 (invoice PDFs) — follow-ups

- [ ] ⚠️ **`update_fields` is an allowlist, and that is not obvious.** The issue path saves through an
      explicit list of fields, so the new letterhead snapshots were computed, assigned to the model
      instance and then silently discarded on save. No exception, no trace, and the PDF showed a blank
      bank block for every invoice, forever. A snapshot that is not *named* in that list does not exist.
      **This is the single most dangerous pattern in the invoicing code.**
- [ ] **The rupee sign fails twice, in two different ways.** WeasyPrint's default font has no U+20B9 and
      prints a box; the Windows console is cp1252 and raises `UnicodeEncodeError` on the same
      character. `check_pdf_engine` detects the first by comparing two renders; the second was found by
      the diagnostic itself crashing. Neither is a WeasyPrint bug, and neither would have surfaced in a
      test that only asserted "a PDF came out".
- [ ] **The template picker is driven by the API, not by hardcoded options.** `/invoices/pdf-templates/`
      means a third layout is one HTML file plus one registry entry. The static `<option>` pair in the
      markup is a deliberate fallback for when that call fails.
- [ ] ⚠️ **PDF download is `fetch` + Blob, never a bare link.** An `<a href="/invoices/1/pdf/">` sends no
      Authorization header and answers 401. The user sees a JSON error page where a file should be.
- [ ] ⚠️ **The PDF is cached for a day, keyed by invoice + template + status + row count.** An issued
      invoice is immutable so this is safe, but the key deliberately includes status and line count: a
      cache keyed on the id alone would serve a draft's rendering to the issued document that replaced it.
- [ ] **The cache is a database table, so it can fail.** `django_cache` going missing would otherwise turn
      every PDF request into a 500 because of a speed optimisation. Both helpers swallow their errors
      and degrade to "not cached".
- [ ] **The backfill stamps today's bank details onto historical invoices.** Deliberate: wrong history
      beats a blank box on a document the customer already holds. Recorded on the migration itself.
- [ ] **WeasyPrint 70 changed `FontConfiguration`**: `font_directory` is gone and `font_map` is a raw
      CFFI pointer that cannot be iterated from Python. Font introspection is therefore not available,
      which is why glyph coverage is tested by *rendering* instead of by reading font tables.
- [ ] **The engine check is a deployment tool, not a test.** `python manage.py check_pdf_engine` is the
      one command that answers "why is the PDF button doing nothing". It exits 1 when Pango is missing
      and 2 when the rupee glyph is unavailable, so a deploy script can gate on it.

---

## 10. Purchases (2.2) — removed, restorable

The module was built, then **removed in full on 2026-10-12**: not needed in this
project at the time. Everything here is what it would take to bring it back.

- [x] **The database tables were left in place on purpose.** `purchases_purchase`
      and `purchases_purchaseitem` still exist, holding 2 rows. Django ignores
      tables no app claims. Keeping them means a restore is `git revert` plus
      two migrations, with **no data loss** — which matters after this project
      has already lost `Docs/` to a delete and a database to a reset.
- [x] **The reusable parts were kept outside the module, so a restore is smaller
      than it looks:** `Item.purchase_price`, the `PURCHASE*` movement types,
      `SourceType.PURCHASE`, and `_stock_lines_for_document`.
- [ ] **To restore:** revert the removal commit, then re-register
      `apps.purchases` in `INSTALLED_APPS` and `config/urls.py`, and re-add the
      CI concurrency line. The three migrations are still in git history and
      would re-apply cleanly — but check whether `0003`'s corrected lifecycle
      constraints are still wanted, or whether the model has moved on since.
- [ ] ⚠️ **`PURCHASE_RETURN` is a hard dependency for 2.4.** The enum value is
      retained but nothing writes it. Debit notes that represent a purchase
      return will need `apps/purchases` back, or a home of their own.
- [ ] **The principle is the durable part, not the module.** A supplier's bill is
      transcribed, so the typed figures are authoritative and the calculator is a
      comparator that never overwrites them; both versions are stored and the gap
      is surfaced. The same rule should govern any future document carrying
      typed money. `compare_entered_vs_computed` lived in
      `apps/purchases/services/divergence.py` with 16 pure tests — if that file
      is ever wanted back on its own, it has no dependency on the module.
