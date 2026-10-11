/**
 * Frontend tests for the purchase UI (2.2).
 *
 * The load-bearing claim this file has to defend is that **entered amounts are
 * never overwritten by the calculator**. That is the whole reason the module
 * looks the way it does, and it is exactly the sort of thing that parses
 * perfectly while being wrong - so it is tested directly rather than by reading.
 *
 * Run: npm test (or `node --test tests/purchases.test.js`).
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

/**
 * Load the real page and the real scripts into jsdom.
 *
 * Scripts are evaluated in the same order the HTML lists them (config, api,
 * ui, page). Getting that order wrong makes `Auth` undefined at load and the
 * page silently does nothing - which is how a whole page can pass a smoke test
 * and render blank.
 */
async function loadPage() {
  const dom = new JSDOM(readPage("purchases/index.html"), {
    url: "http://localhost/purchases/index.html",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;

  window.API_BASE_URL = "http://api.test/api/v1";

  // Record every URL the page actually asks for.
  //
  // This stub used to answer the same canned body for every call and ignore the
  // URL completely, which meant a page whose every endpoint was misspelled still
  // passed: the tests below only ever called the pure helpers. The result was a
  // page that shipped requesting `/api/v1/api/v1/purchases/` - a 404 for the
  // entire screen - while all ten tests were green. Capturing the URL is what
  // turns "does it parse" into "does it ask the right thing".
  const requested = [];
  const respond = (url) => {
    requested.push(url);
    const path = url.replace("http://api.test/api/v1", "");
    let body = { results: [], count: 0 };
    if (path.startsWith("/meta/units/")) body = { results: [{ id: 1, name: "PCS" }] };
    if (path.startsWith("/meta/gst-rates/")) body = { results: [{ id: 1, code: "18", rate: 18 }] };
    return {
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => body,
    };
  };
  window.fetch = async (url) => respond(String(url));
  window.__requested = requested;
  window.__respond = respond;

  window.eval(readPage("shared/js/config.js"));
  window.eval(
    readPage("shared/js/api.js") +
      "\nwindow.Auth = Auth; window.apiRequest = apiRequest;" +
      "\nwindow.withRetryOnThrottle = withRetryOnThrottle; window.ApiError = ApiError;"
  );
  window.eval(
    readPage("shared/js/ui.js") +
      "\nwindow.Auth = Auth; window.requireLogin = requireLogin; window.renderNav = renderNav;" +
      "\nwindow.h = h; window.debounce = debounce; window.showToast = showToast;" +
      "\nwindow.clearErrors = clearErrors; window.showFieldErrors = showFieldErrors;" +
      "\nwindow.setBusy = setBusy; window.formatINR = formatINR;"
  );

  // A logged-in session so requireLogin() returns true.
  window.localStorage.setItem("velocity_refresh", "test-refresh");
  window.localStorage.setItem("velocity_access", "test-access");

  window.eval(readPage("purchases/purchases.js"));
  return { window, document: window.document };
}

const HEADER = {
  supplier: "3",
  bill_number: "SUP-001",
  bill_date: "2026-10-01",
  grand_total: "1180.00",
  taxable_total: "1000.00",
  cgst_total: "0.00",
  sgst_total: "0.00",
  igst_total: "180.00",
};

const LINE = {
  item: 7,
  item_name: "Widget",
  item_type: "PRODUCT",
  quantity: "10.000",
  unit_price: "100.00",
  line_discount: "0.00",
  tax_rate: "18.00",
  taxable_value: "1000.00",
  cgst_amount: "0.00",
  sgst_amount: "0.00",
  igst_amount: "180.00",
  total_amount: "1180.00",
};

// ---------------------------------------------------------------------------

test("purchases: the payload sends the entered figures verbatim", async () => {
  const { window } = await loadPage();
  const payload = window.__testBuildPayload([{ ...LINE }], HEADER);

  assert.equal(
    payload.grand_total,
    "1180.00",
    "the typed grand total must reach the API untouched"
  );
  assert.equal(payload.taxable_total, "1000.00");
  assert.equal(payload.igst_total, "180.00");
  assert.equal(payload.bill_number, "SUP-001");
  assert.equal(payload.items.length, 1);
  assert.equal(payload.items[0].total_amount, "1180.00");
});

test("purchases: blank money fields become zero rather than empty strings", async () => {
  const { window } = await loadPage();
  // An empty string in a DecimalField is a 400; "" | "0.00" keeps the payload
  // valid so one untouched field does not block saving the whole bill.
  const payload = window.__testBuildPayload([{ ...LINE }], { ...HEADER, cgst_total: "" });
  assert.equal(payload.cgst_total, "0.00");
});

test("purchases: a free-text line carries its description and no item id", async () => {
  const { window } = await loadPage();
  const freight = { ...LINE, item: null, item_name: "Freight", hsn_sac_code: "996511", unit: "BOX", total_amount: "50.00" };
  const payload = window.__testBuildPayload([freight], HEADER);

  assert.equal(payload.items[0].item, undefined, "a free-text line must not claim an item");
  assert.equal(payload.items[0].item_name, "Freight");
  assert.equal(payload.items[0].hsn_sac_code, "996511");
});

test("purchases: itc_eligible is opt-out, defaulting to claimable", async () => {
  const { window } = await loadPage();
  const claimed = window.__testBuildPayload([{ ...LINE }], HEADER);
  assert.equal(claimed.items[0].itc_eligible, true);

  const excluded = window.__testBuildPayload([{ ...LINE, itc_eligible: false }], HEADER);
  assert.equal(excluded.items[0].itc_eligible, false);
});

test("purchases: the update-purchase-price flag is sent when ticked", async () => {
  const { window } = await loadPage();
  assert.equal(
    window.__testBuildPayload([{ ...LINE }], HEADER).update_item_purchase_price,
    false
  );
  assert.equal(
    window.__testBuildPayload([{ ...LINE }], { ...HEADER, update_item_purchase_price: true })
      .update_item_purchase_price,
    true
  );
});

test("purchases: a divergence warning says the numbers are kept as entered", async () => {
  const { window } = await loadPage();
  const result = window.__testRenderDivergenceVerdict({
    has_divergence: true,
    max_divergence: "12.00",
  });

  assert.match(result.text, /will be saved exactly as entered/i);
  assert.match(result.text, /12\.00/);
  assert.ok(
    result.className.includes("text-warning"),
    "a mismatch is a warning, not an error - the bill is still valid"
  );
  assert.ok(
    !result.className.includes("text-danger"),
    "it must not read as a failure; the supplier's bill is allowed to differ"
  );
});

test("purchases: figures that agree read as agreement, in green", async () => {
  const { window } = await loadPage();
  const result = window.__testRenderDivergenceVerdict({ has_divergence: false });
  assert.match(result.text, /agree/i);
  assert.ok(result.className.includes("text-ok"));
});

test("purchases: the status badge distinguishes all three states", async () => {
  const { window } = await loadPage();
  assert.match(window.__testStatusBadge("DRAFT").text, /Draft/);
  assert.match(window.__testStatusBadge("POSTED").text, /Posted/);
  assert.match(window.__testStatusBadge("CANCELLED").text, /Cancelled/);

  // "Posted" and "Cancelled" must not look alike - that is the difference
  // between a bill that added stock and one that removed it.
  const posted = window.__testStatusBadge("POSTED").className;
  const cancelled = window.__testStatusBadge("CANCELLED").className;
  assert.notEqual(posted, cancelled);
});

test("purchases: a refusal names the item and the resulting balance", async () => {
  const { window } = await loadPage();
  const lines = window.__testCancelDetailLines([
    {
      item_name: "Blue Widget",
      available: "2.000",
      would_remove: "10.000",
      resulting: "-8.000",
    },
  ]);
  assert.equal(lines.length, 1);
  assert.match(lines[0], /Blue Widget/);
  assert.match(lines[0], /2\.000/);
  assert.match(lines[0], /-8\.000/);
});

test("purchases: an empty cancellation list produces no lines, not a crash", async () => {
  const { window } = await loadPage();
  assert.deepEqual([...window.__testCancelDetailLines([])], []);
});

// --- Wiring -----------------------------------------------------------------
//
// These exist because the page shipped with every URL doubled up to
// `/api/v1/api/v1/...`. Every call 404'd, so the screen was entirely dead, and
// all ten tests above still passed: they only exercised pure helpers, and the
// fetch stub never looked at the URL. The defect was only visible in a browser.

/** Wait for the page's boot to settle: everything queued its work through promises. */
async function settle(window, ms = 60) {
  await new Promise((r) => setTimeout(r, ms));
}

test("purchases: no request doubles up the /api/v1 prefix", async () => {
  const { window } = await loadPage();
  await settle(window);

  const urls = window.__requested;
  assert.ok(urls.length > 0, "the page issued no requests, so this proves nothing");

  for (const url of urls) {
    assert.ok(
      !url.includes("/api/v1/api/v1"),
      `URL is doubled up: ${url}\n` +
        `apiRequest() already prefixes API_BASE_URL - pass "/purchases/", not "/api/v1/purchases/".`
    );
  }
});

test("purchases: boot loads the purchase list and the item catalogue", async () => {
  const { window } = await loadPage();
  await settle(window);

  const paths = window.__requested.map((u) => u.replace("http://api.test/api/v1", ""));
  assert.ok(
    paths.some((p) => p.startsWith("/purchases/")),
    `expected the purchase list, got: ${JSON.stringify(paths)}`
  );
  // The line item picker is built from the catalogue. Without it every line
  // falls back to "Other (free text)" and stock never moves.
  assert.ok(paths.some((p) => p.startsWith("/items/")), "item picker was never populated");
  // A purchase takes its unit and rate from the catalogue item, not from a
  // picker of its own, so there is deliberately no /meta/units/ or
  // /meta/gst-rates/ call here.
  assert.ok(!paths.some((p) => p.startsWith("/meta/units/")), "purchase should not fetch units");
});

test("purchases: the list request carries paging, not a bare fetch", async () => {
  const { window } = await loadPage();
  await settle(window);

  const list = window.__requested
    .map((u) => u.replace("http://api.test/api/v1", ""))
    .find((p) => p.startsWith("/purchases/?"));
  assert.ok(list, "no purchase list request was made");
  // Without page_size the list shows 20 rows and "next" silently does nothing.
  assert.match(list, /page_size=20/);
  assert.match(list, /page=1/);
});

test("purchases: the supplier picker asks for suppliers and nobody else", async () => {
  const { window } = await loadPage();
  await settle(window);
  window.document.getElementById("new-purchase-btn").click();
  await settle(window);

  const supplierCall = window.__requested
    .map((u) => u.replace("http://api.test/api/v1", ""))
    .find((p) => p.startsWith("/parties/?"));

  assert.ok(supplierCall, "opening a purchase never loaded the supplier list");
  assert.match(supplierCall, /party_type=SUPPLIER/);
  // Walk-ins have no GSTIN, so a bill for one can never be a real purchase.
  assert.match(supplierCall, /is_active=true/);
});

// --- Drawer visibility -------------------------------------------------------
//
// The page shipped with a drawer that never appeared. Every handler ran
// correctly - the supplier request went out, the log looked healthy - and the
// user saw nothing at all.
//
// The cause is a collision with the app-wide `.drawer` / `.drawer-panel` rules
// in theme.css, which items.js and parties.js cooperate with: the wrapper is
// `visibility: hidden` and the panel starts at `translateX(100%)`. Removing the
// Tailwind `hidden` class therefore does nothing visible, because `visibility`
// is not `display`, and the panel stays parked off the right edge of the screen.
//
// jsdom does not compute layout, so these assert the *mechanism* - the classes
// and inline transform the animation depends on - which is exactly what was
// missing.

test("purchases: opening the drawer actually brings the panel on screen", async () => {
  const { window, document } = await loadPage();
  await settle(window);

  const drawer = document.getElementById("drawer");
  const panel = drawer.querySelector(".drawer-panel");

  window.document.getElementById("new-purchase-btn").click();
  await settle(window);

  assert.ok(!drawer.classList.contains("hidden"), "drawer wrapper was never revealed");
  // The decisive assertion: without this the panel is off-screen even though
  // everything else "worked".
  assert.equal(
    panel.style.transform,
    "translateX(0)",
    "panel is still translated off-screen - the user sees a blank screen"
  );
});

test("purchases: the panel ships parked off-screen so it cannot flash open", async () => {
  const { window, document } = await loadPage();
  const panel = document.getElementById("drawer").querySelector(".drawer-panel");

  // Inline in the HTML, not applied by JS: this is the closed state, and it has
  // to be the state the browser paints before any script runs.
  assert.match(panel.getAttribute("style") || "", /translateX\(100%\)/);
  assert.ok(panel.classList.contains("transition-transform"), "no transition on the panel");
});

test("purchases: closing the drawer parks it again and unlocks the page", async () => {
  const { window, document } = await loadPage();
  await settle(window);

  const drawer = document.getElementById("drawer");
  const panel = drawer.querySelector(".drawer-panel");

  window.document.getElementById("new-purchase-btn").click();
  await settle(window);
  assert.equal(panel.style.transform, "translateX(0)");
  assert.ok(document.body.classList.contains("no-scroll"), "background should not scroll behind the drawer");

  // The close button in the drawer footer.
  drawer.querySelector("footer [data-close]").click();
  await settle(window, 260);

  assert.equal(panel.style.transform, "translateX(100%)");
  assert.ok(drawer.classList.contains("hidden"), "drawer was never hidden again");
  assert.ok(!document.body.classList.contains("no-scroll"), "page stayed scroll-locked");
});

// --- Page chrome -------------------------------------------------------------
//
// The purchases page was the only page in the app whose header did not match
// the rest: a bare `<div id="nav">` with no `bg-navy`, so the navigation bar
// rendered unstyled against a different page background while every sibling
// page looked right. A shared chrome file is the only place a single page can
// drift away from the others, so the comparison is made against those files
// rather than against a remembered template.

test("purchases: the navbar matches every other page", async () => {
  const { readPage: read } = await import("node:fs/promises").then(() => ({
    readPage: (r) => require("node:fs").readFileSync(path.join(ROOT, r), "utf8"),
  }));
  const html = read("purchases/index.html");

  assert.match(html, /<header id="nav" class="bg-navy"><\/header>/);
  assert.match(html, /<body class="min-h-screen bg-page">/);
  assert.match(html, /<title>Purchases \| Velocity<\/title>/);
  assert.doesNotMatch(html, /<div id="nav">/);
});

test("purchases: the drawer is wide enough to read, not a sliver", async () => {
  const { document } = await loadPage();
  const panel = document.getElementById("drawer").querySelector(".drawer-panel");
  const cls = panel.className;

  // It used to be max-w-3xl (768px) for a nine-column line editor.
  assert.match(cls, /max-w-6xl/);
  assert.doesNotMatch(cls, /max-w-3xl/);
});

test("purchases: the due date is marked required in the markup", async () => {
  const { document } = await loadPage();
  const due = document.getElementById("f-due_date");

  assert.ok(due.hasAttribute("required"), "due date input is not required");
  assert.match(document.querySelector('label[for="f-due_date"]').innerHTML, /\*/);
});

test("purchases: the line table header mirrors the cells renderLines emits", async () => {
  const { document } = await loadPage();
  const heads = [...document.querySelectorAll("#lines-wrap thead th")]
    .filter((th) => !th.querySelector(".sr-only")) // the remove column's a11y label
    .map((th) => th.textContent.trim().toLowerCase())
    .filter(Boolean);

  // One heading per LINE_HEADERS entry. It used to collapse four tax columns
  // into a single "Tax", which shifted every figure under the wrong label.
  assert.deepEqual(heads, [
    "item", "qty", "rate", "taxable", "cgst", "sgst", "igst", "total",
  ]);
});

test("purchases: a blank optional field is sent as null, not an empty string", async () => {
  const { window } = await loadPage();
  // DRF reads "" as an unparseable date and answers 400 with a message about
  // formats - blaming the user for a field they never touched.
  const payload = window.__testBuildPayload([{ ...LINE }], { ...HEADER, due_date: "" });
  assert.equal(payload.due_date, null);
  assert.equal(payload.notes, null);
});