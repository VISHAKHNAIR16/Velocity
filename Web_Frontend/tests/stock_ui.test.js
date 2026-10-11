/**
 * Frontend tests for the stock UI (2.1.4).
 *
 * Why jsdom rather than "check the file parses"
 * ---------------------------------------------
 * `node --check` only proves the file is syntactically valid. Every bug worth
 * catching here is a *wording* or *wiring* bug - a badge that says "in stock"
 * when stock is short, a highlight that lands on the wrong line, a number
 * rendered as "NaN". All of those parse perfectly and all of them are the
 * failure a shopkeeper actually sees. So the real script is loaded into a DOM
 * and driven the way a user drives it.
 *
 * No browser automation is available in this environment, so this is the
 * strongest check that can run in CI. The visual sign-off in
 * docs/QA_CHECKLIST.md is still required before 2.1.5 is called done.
 *
 * Run: node --test Web_Frontend/tests/
 */

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "..");

function readPage(relative) {
  return fs.readFileSync(path.join(ROOT, relative), "utf8");
}

// ---------------------------------------------------------------------------
// A jsdom page with the real scripts loaded and fetch stubbed.
// ---------------------------------------------------------------------------

/**
 * Build a page from a real HTML file, then run the page's scripts against it.
 *
 * `requests` records every fetch so a test can assert *which* endpoint was
 * called and with what body - which is how the "the modal posts the mode the
 * user picked" test works.
 */
async function loadPage(htmlFile, scriptFiles, { apiBase = "http://api.test/api/v1" } = {}) {
  const html = readPage(htmlFile);
  const dom = new JSDOM(html, {
    url: "http://localhost/items/index.html",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  const requests = [];

  window.API_BASE_URL = apiBase;

  // The nav/header helpers touch localStorage and location; stub just enough for
  // the page to initialise without a backend.
  window.localStorage.clear();

  window.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ url, method: options.method || "GET", body });
    const handler = window.__fetchHandler;
    if (handler) return handler(url, options, body);
    return jsonResponse(200, { success: true, results: [], count: 0 });
  };

  // `requireLogin()` reads `Auth` from api.js, and separate `eval` calls do not
  // share a `const` lexical binding - so each script is evaluated with an
  // explicit export appended, and in the same order the real pages load them
  // (config -> api -> ui -> page). A test-harness detail, not an app change.
  window.API_BASE_URL = apiBase;
  window.eval(fs.readFileSync(path.join(ROOT, "shared/js/config.js"), "utf8"));
  window.eval(
    fs.readFileSync(path.join(ROOT, "shared/js/api.js"), "utf8") +
      "\nwindow.Auth = Auth; window.apiRequest = apiRequest;" +
      "\nwindow.withRetryOnThrottle = withRetryOnThrottle; window.ApiError = ApiError;"
  );
  window.eval(
    fs.readFileSync(path.join(ROOT, "shared/js/ui.js"), "utf8") +
      "\nwindow.Auth = Auth; window.requireLogin = requireLogin; window.renderNav = renderNav;" +
      "\nwindow.h = h; window.debounce = debounce; window.showToast = showToast;" +
      "\nwindow.clearErrors = clearErrors; window.showFormMessage = showFormMessage;" +
      "\nwindow.showFieldErrors = showFieldErrors; window.setBusy = setBusy;"
  );

  // A logged-in session, so `requireLogin()` returns true instead of redirecting.
  window.localStorage.setItem("velocity_refresh", "test-refresh-token");
  window.localStorage.setItem("velocity_access", "test-access-token");

  for (const file of scriptFiles) {
    window.eval(readPage(file));
  }

  return { dom, window, document: window.document, requests };
}

function jsonResponse(status, body, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => headers[k] ?? null },
    json: async () => body,
  };
}

// The item-search payload shape the billing form consumes.
const TRACKED_ITEM = {
  id: 7,
  name: "Blue Widget",
  unit: "PCS",
  item_type: "PRODUCT",
  tax_rate: "5.00",
  sales_price: "249.50",
  hsn_sac_code: "610510",
  current_stock: "10.000",
  tracks_stock: true,
};

// ---------------------------------------------------------------------------
// billing.js: the stock badge
// ---------------------------------------------------------------------------

test("billing: a line with enough stock shows the balance and no warning", async () => {
  const { window } = await loadPage("invoices/billing.html", ["invoices/billing.js"]);
  const badge = window.__testRenderStockBadge({
    item: TRACKED_ITEM,
    quantity: "3.000",
  });
  assert.match(badge.textContent, /10 PCS in stock/);
  assert.doesNotMatch(badge.textContent, /rejected|go to/);
  assert.doesNotMatch(badge.className, /text-danger|text-warning/, "a healthy badge must not be styled as a warning");
});

test("billing: overselling under ALLOW warns in amber, and says what stock will be", async () => {
  const { window } = await loadPage("invoices/billing.html", ["invoices/billing.js"]);
  const badge = window.__testRenderStockBadge({
    item: TRACKED_ITEM,
    quantity: "12.000",
  });
  // The number the user will actually see on the shelf matters: "stock will go
  // to -2" tells them what to count, "not enough stock" does not.
  assert.match(badge.textContent, /stock will go to -2/);
  assert.match(badge.textContent, /Only 10/);
  assert.ok(badge.className.includes("text-warning"), "ALLOW is a warning, not an error");
  assert.ok(!badge.className.includes("text-danger"), "ALLOW must not be styled as an error");
});

test("billing: overselling under BLOCK is red and promises rejection", async () => {
  const { window } = await loadPage("invoices/billing.html", ["invoices/billing.js"]);
  const badge = window.__testRenderStockBadge({
    item: TRACKED_ITEM,
    quantity: "12.000",
    stockBlocked: true,
  });
  assert.match(badge.textContent, /will be rejected/);
  assert.ok(badge.className.includes("text-danger"), "BLOCK is styled as an error");
});

test("billing: a server-named shortage row overrides the local badge", async () => {
  // The server looked with a lock; a locally computed badge is only a guess.
  // When they disagree the server wins, which is what applyStockShortage does.
  const { window } = await loadPage("invoices/billing.html", ["invoices/billing.js"]);
  const badge = window.__testRenderStockBadge({
    item: TRACKED_ITEM,
    quantity: "1.000",
    stockError: "Short by 4 PCS",
  });
  assert.match(badge.textContent, /Short by 4 PCS/);
  assert.ok(badge.className.includes("text-danger"));
  assert.doesNotMatch(badge.textContent, /10 in stock/);
});

test("billing: untracked and service lines get no stock badge at all", async () => {
  const { window } = await loadPage("invoices/billing.html", ["invoices/billing.js"]);
  // "No warning" and "in stock" are different claims; a service has neither.
  const untracked = window.__testRenderStockBadge({
    item: { ...TRACKED_ITEM, tracks_stock: false },
    quantity: "999.000",
  });
  assert.equal(untracked.textContent, "");
  assert.equal(untracked.childNodes.length, 0);
});

test("billing: stock quantities never render as NaN", async () => {
  const { window } = await loadPage("invoices/billing.html", ["invoices/billing.js"]);
  // A NaN on screen is the single most alarming thing this widget could show:
  // it means the arithmetic broke, not that stock is wrong.
  for (const quantity of ["", "abc", null, undefined, "0"]) {
    const badge = window.__testRenderStockBadge({
      item: { ...TRACKED_ITEM, current_stock: null },
      quantity,
    });
    assert.doesNotMatch(badge.textContent, /NaN|undefined/);
  }
});

// ---------------------------------------------------------------------------
// The aggregation rule, which is the part most likely to be got wrong.
// ---------------------------------------------------------------------------

test("billing: the same item on three lines is checked as the sum, not per line", async () => {
  const { window } = await loadPage("invoices/billing.html", ["invoices/billing.js"]);
  // 3 x 4 = 12 against a stock of 10. Every line looks fine on its own.
  const perLine = [4, 4, 4].map((quantity) =>
    window.__testRenderStockBadge({ item: TRACKED_ITEM, quantity: String(quantity) })
  );
  perLine.forEach((badge) => {
    assert.match(badge.textContent, /10 PCS in stock/, "each line alone is fine");
  });

  const summed = window.__testApplyStockCheck({
    stock_check: [
      {
        item_id: 7,
        item_name: "Blue Widget",
        unit: "PCS",
        available: "10.000",
        requested: "12.000",
        stock_after: "-2.000",
        will_go_negative: true,
        shortfall: "2.000",
        will_be_rejected: false,
      },
    ],
    lines: [
          { mode: "item", item: { id: 7, tracks_stock: true, current_stock: "10.000", unit: "PCS" }, quantity: "4" },
          { mode: "item", item: { id: 7, tracks_stock: true, current_stock: "10.000", unit: "PCS" }, quantity: "4" },
          { mode: "item", item: { id: 7, tracks_stock: true, current_stock: "10.000", unit: "PCS" }, quantity: "4" },
        ],
  });
  assert.equal(summed.warned, 3, "all three lines of the same item must be flagged");
  assert.match(summed.example, /stock will go to -2/i);
});

test("billing: a shortage detail names only the item it is about", async () => {
  const { window } = await loadPage("invoices/billing.html", ["invoices/billing.js"]);
  const result = window.__testApplyShortage({
    details: [
      { item_id: 7, shortfall: "4.000", unit: "PCS" },
      { item_id: 9, shortfall: "1.000", unit: "KG" },
    ],
    lines: [
      { mode: "item", item: { id: 7, tracks_stock: true, current_stock: "10.000", unit: "PCS" } },
      { mode: "item", item: { id: 8, tracks_stock: true, current_stock: "10.000", unit: "PCS" } },
      { mode: "item", item: { id: 9, tracks_stock: true, current_stock: "10.000", unit: "KG" } },
    ],
  });
  // Spread into this realm's array before comparing: arrays built inside the
  // jsdom realm have a different Array.prototype, and `deepStrictEqual` checks
  // prototypes - so `[7, 9] !== [7, 9]` when they come from two realms. The
  // contents are identical; only the identity of Array.prototype differs.
  assert.deepEqual([...result.flagged], [7, 9], "only the items the server named are flagged");
  assert.match(result.messages[7], /Short by 4 PCS/);
  assert.match(result.messages[9], /Short by 1 KG/);
});

// ---------------------------------------------------------------------------
// items.js: the adjust modal
// ---------------------------------------------------------------------------

test("items: the adjust preview shows before -> after for Change by", async () => {
  const { window, document } = await loadPage("items/index.html", ["items/items.js"]);
  window.__testSetEditingItem({
    id: 7, name: "Blue Widget", unit: "PCS",
    current_stock: "10.000", tracks_stock: true,
    item_type: "PRODUCT", unit_locked: true, is_low_stock: false,
  });

  const before = document.getElementById("adjust-before").textContent;
  const after = window.__testPreviewAdjust("CHANGE", "-2.5");
  assert.equal(before, "10.000");
  assert.equal(after, "7.500");
});

test("items: the adjust preview shows before -> after for Set to", async () => {
  const { window, document } = await loadPage("items/index.html", ["items/items.js"]);
  window.__testSetEditingItem({
    id: 7, name: "Blue Widget", unit: "PCS",
    current_stock: "10.000", tracks_stock: true,
    item_type: "PRODUCT", unit_locked: false, is_low_stock: false,
  });
  // "Set to 48" and "change by 38" are different intentions that look identical
  // in a number box, which is exactly why the mode is explicit.
  assert.equal(window.__testPreviewAdjust("SET", "48"), "48.000");
  assert.equal(window.__testPreviewAdjust("CHANGE", "38"), "48.000");
  assert.equal(window.__testPreviewAdjust("SET", "3.5"), "3.500");
});

test("items: a nonsense adjustment never previews as a number", async () => {
  const { window } = await loadPage("items/index.html", ["items/items.js"]);
  window.__testSetEditingItem({
    id: 7, current_stock: "10.000", unit: "PCS", tracks_stock: true,
    item_type: "PRODUCT", unit_locked: false,
  });
  // Showing "NaN" in the preview would look like the app had lost the item's
  // stock entirely.
  for (const raw of ["", "abc", "-", "1.2.3"]) {
    assert.equal(window.__testPreviewAdjust("CHANGE", raw), "—", `"${raw}" must not preview`);
  }
});

test("items: an adjustment that would go negative is flagged in the preview", async () => {
  const { window } = await loadPage("items/index.html", ["items/items.js"]);
  window.__testSetEditingItem({
    id: 7, current_stock: "3.000", unit: "PCS", tracks_stock: true,
    item_type: "PRODUCT", unit_locked: false,
  });
  assert.equal(window.__testPreviewAdjustIsNegative("CHANGE", "-5"), true);
  assert.equal(window.__testPreviewAdjustIsNegative("CHANGE", "-1"), false);
  assert.equal(window.__testPreviewAdjustIsNegative("SET", "-4"), true);
});

// ---------------------------------------------------------------------------
// items.js: read-only stock and the unit lock
// ---------------------------------------------------------------------------

test("items: a tracked item in edit mode cannot have its stock typed in", async () => {
  const { window, document } = await loadPage("items/index.html", ["items/items.js"]);
  window.__testApplyStockMode({
    id: 7, current_stock: "10.000", unit: "PCS", tracks_stock: true,
    item_type: "PRODUCT", unit_locked: false,
  });
  const box = document.getElementById("f-current_stock");
  assert.equal(box.readOnly, true, "stock must not be editable once a ledger exists");
  assert.ok(
    !document.getElementById("stock-readonly-panel").classList.contains("hidden"),
    "the read-only panel with Adjust/History must be shown",
  );
});

test("items: an untracked item can still have its stock typed in", async () => {
  const { window, document } = await loadPage("items/index.html", ["items/items.js"]);
  window.__testApplyStockMode({
    id: 7, current_stock: null, unit: "PCS", tracks_stock: false,
    item_type: "PRODUCT", unit_locked: false,
  });
  const box = document.getElementById("f-current_stock");
  assert.equal(box.readOnly, false, "with no ledger there is nothing to contradict");
});

test("items: a service never shows stock controls", async () => {
  const { window, document } = await loadPage("items/index.html", ["items/items.js"]);
  window.__testApplyStockMode({
    id: 7, current_stock: null, unit: "PCS", tracks_stock: false,
    item_type: "SERVICE", unit_locked: false,
  });
  assert.ok(document.getElementById("stock-section").classList.contains("hidden"));
  assert.ok(document.getElementById("stock-readonly-panel").classList.contains("hidden"));
});

test("items: the unit is disabled and explained once history exists", async () => {
  const { window, document } = await loadPage("items/index.html", ["items/items.js"]);
  window.__testApplyStockMode({
    id: 7, current_stock: "10.000", unit: "PCS", tracks_stock: true,
    item_type: "PRODUCT", unit_locked: true,
  });
  assert.equal(document.getElementById("f-unit").disabled, true);
  assert.ok(
    !document.getElementById("unit-locked-note").classList.contains("hidden"),
    "a disabled control with no explanation reads as a broken app",
  );
});

test("items: current_stock is never sent on edit", async () => {
  const { window } = await loadPage("items/index.html", ["items/items.js"]);
  const payload = window.__testBuildPayload({
    editing: true,
    fields: {
      name: "Blue Widget", item_type: "PRODUCT", item_code: "", barcode: "",
      hsn_sac_code: "610510", service_description: "", unit: "PCS",
      sales_price: "249.50", purchase_price: "", price_includes_tax: false,
      tax_rate: "5.00", current_stock: "999", low_stock_threshold: "",
      track_stock: true,
    },
  });
  // Sending a create-only field invites the server to reject a value the user
  // could not even edit.
  assert.ok(!("current_stock" in payload), "current_stock must be omitted on edit");
});

test("items: a service payload never claims to track stock", async () => {
  const { window } = await loadPage("items/index.html", ["items/items.js"]);
  const payload = window.__testBuildPayload({
    editing: false,
    fields: {
      name: "Consulting", item_type: "SERVICE", item_code: "", barcode: "",
      hsn_sac_code: "998311", service_description: "Advisory", unit: "HRS",
      sales_price: "5000.00", purchase_price: "", price_includes_tax: false,
      tax_rate: "18.00", current_stock: "0", low_stock_threshold: "",
      track_stock: true,
    },
  });
  assert.equal(payload.track_stock, false);
  assert.equal(payload.current_stock, null);
});

// ---------------------------------------------------------------------------
// The stock history drawer
// ---------------------------------------------------------------------------

test("items: the history footer admits when the list is truncated", async () => {
  const { window } = await loadPage("items/index.html", ["items/items.js"]);
  // A truncated list that looks complete is how a shopkeeper concludes a
  // movement never happened.
  const truncated = window.__testHistoryFooter({ count: 1438, returned: 200 });
  assert.match(truncated, /200/);
  assert.match(truncated, /1,?438/);

  const complete = window.__testHistoryFooter({ count: 12, returned: 12 });
  assert.doesNotMatch(complete, /Showing the/);
  assert.match(complete, /12 movements/);
});

test("items: history rows keep the sign and the running balance readable", async () => {
  const { window } = await loadPage("items/index.html", ["items/items.js"]);
  const rows = window.__testRenderHistoryRows([
    { movement_type: "OPENING", quantity_change: "100.000", stock_after: "100.000", source_label: "OPENING" },
    { movement_type: "SALE", quantity_change: "-12.000", stock_after: "88.000", source_label: "INV-0001" },
    { movement_type: "SALE_REVERSAL", quantity_change: "12.000", stock_after: "100.000", source_label: "INV-0001" },
  ]);
  assert.equal(rows.length, 3);
  assert.match(rows[1].amount, /-12\.000/);
  assert.match(rows[1].balance, /88\.000/);
  assert.ok(rows[1].negative, "a sale must render as a decrease");
  assert.ok(!rows[2].negative, "a reversal must not render as a decrease");
  assert.match(rows[0].label, /Opening/);
  assert.match(rows[2].label, /Cancellation/, "a reversal reads as a cancellation to a user");
});