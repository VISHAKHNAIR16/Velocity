/**
 * Frontend tests for the invoice PDF picker (2.3).
 *
 * The bug these exist to prevent is specific and has already happened in this
 * project twice: a page whose every request 404'd while its test suite was
 * entirely green. The earlier purchase tests stubbed `fetch` so that it ignored
 * the URL, which meant a page could not possibly have worked and still passed.
 * So this harness records every URL, and the assertions below are about
 * *wiring* - not about helper functions that were always fine.
 *
 * Run: npm test (or `node --test tests/invoice_pdf.test.js`).
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
 * Load the real billing page and the real scripts into jsdom.
 *
 * The fetch stub records URLs and answers the two endpoints the PDF feature
 * uses. A stub that ignored the URL is what hid the doubled-`/api/v1` bug for
 * an entire phase, so recording is not optional here.
 */
async function loadPage() {
  const dom = new JSDOM(readPage("invoices/billing.html"), {
    url: "http://localhost/invoices/billing.html?id=7",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;

  window.API_BASE_URL = "http://api.test/api/v1";

  const requested = [];
  const blobs = [];
  window.fetch = async (url, options) => {
    requested.push(String(url));
    const path = String(url).replace("http://api.test/api/v1", "");
    if (path.startsWith("/invoices/pdf-templates/")) {
      return {
        ok: true,
        status: 200,
        headers: { get: () => null },
        json: async () => ({
          default: "classic",
          templates: [
            { key: "classic", label: "Classic", description: "Formal, for B2B." },
            { key: "modern", label: "Modern", description: "Clean, for retail." },
          ],
        }),
      };
    }
    if (path.includes("/pdf/")) {
      blobs.push(path);
      return { ok: true, status: 200, blob: async () => ({ size: 1234, type: "application/pdf" }) };
    }
    // Anything else the page asks for on boot.
    return {
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => ({ id: 7, invoice_number: "INV/26-27/00007", status: "ISSUED", items: [] }),
    };
  };
  window.__requested = requested;
  window.__blobs = blobs;

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

  window.localStorage.setItem("velocity_refresh", "test-refresh");
  window.localStorage.setItem("velocity_access", "test-access");

  // Evaluate the whole page and take the real functions off its test seam.
  // Copying them out with a regex worked once and broke the next time the
  // function gained a line.
  window.eval(readPage("invoices/billing.js"));

  return { window, document: window.document };
}

async function settle(window, ms = 40) {
  await new Promise((r) => setTimeout(r, ms));
}

// --- Markup -----------------------------------------------------------------

test("invoice pdf: the picker offers both templates out of the box", async () => {
  const { document } = await loadPage();
  const select = document.getElementById("pdf-template");
  const values = [...select.options].map((o) => o.value);
  // The static options matter: if the API call fails, the picker must still work.
  assert.deepEqual(values, ["classic", "modern"]);
});

test("invoice pdf: the button says Download, not Print", async () => {
  const { document } = await loadPage();
  assert.match(document.getElementById("print-btn").textContent, /Download PDF/i);
});

test("invoice pdf: the old window.print() call is gone", () => {
  // window.print() prints the screen, not a PDF: it loses the letterhead, the
  // bank block and the signature, which is the whole reason this endpoint exists.
  const source = readPage("invoices/billing.js");
  assert.doesNotMatch(source, /window\.print\(\)/);
});

// --- Wiring -----------------------------------------------------------------

test("invoice pdf: templates load from the server, not a doubled prefix", async () => {
  const { window } = await loadPage();
  await window.__testPdf.loadPdfTemplates();

  const call = window.__requested.find((u) => u.includes("pdf-templates"));
  assert.ok(call, "the template list was never requested");
  assert.ok(!call.includes("/api/v1/api/v1"), `URL is doubled up: ${call}`);
  assert.equal(call, "http://api.test/api/v1/invoices/pdf-templates/");
});

test("invoice pdf: a failed template fetch leaves the picker usable", async () => {
  const { window, document } = await loadPage();
  window.fetch = async () => {
    throw new Error("offline");
  };
  await window.__testPdf.loadPdfTemplates();
  const select = document.getElementById("pdf-template");
  assert.equal([...select.options].length, 2, "the static options were lost");
});

test("invoice pdf: downloading sends the chosen template", async () => {
  const { window, document } = await loadPage();
  await window.__testPdf.loadPdfTemplates();
  document.getElementById("pdf-template").value = "modern";

  // URL.createObjectURL is absent in jsdom.
  window.URL.createObjectURL = () => "blob:stub";
  window.URL.revokeObjectURL = () => {};

  await window.__testPdf.downloadPdf();

  const call = window.__blobs[0];
  assert.ok(call, "no PDF was requested");
  assert.match(call, /\/invoices\/7\/pdf\//);
  assert.match(call, /template=modern/);
});

test("invoice pdf: the request carries the access token", async () => {
  const { window, document } = await loadPage();
  await window.__testPdf.loadPdfTemplates();

  let seenAuth = null;
  window.fetch = async (url, options) => {
    seenAuth = options && options.headers && options.headers.Authorization;
    return { ok: true, status: 200, blob: async () => ({ size: 1 }) };
  };
  window.URL.createObjectURL = () => "blob:stub";
  window.URL.revokeObjectURL = () => {};

  await window.__testPdf.downloadPdf();
  // A bare <a href> would send no header and answer 401.
  assert.equal(seenAuth, "Bearer test-access");
});

test("invoice pdf: a server error surfaces its own message, not a generic one", async () => {
  const { window, document } = await loadPage();
  await window.__testPdf.loadPdfTemplates();

  let toast = "";
  window.eval(`window.__toast = "";`);
  window.showToast = (message) => {
    window.__toast = message;
    toast = message;
  };
  window.fetch = async () => ({
    ok: false,
    status: 503,
    json: async () => ({
      error: "PDF_ENGINE_UNAVAILABLE",
      message: "PDF generation is not available on this server (ImportError).",
    }),
  });

  await window.__testPdf.downloadPdf();
  // 503 has an action attached to it - the host is missing Pango - and a
  // generic "download failed" would throw that away.
  assert.match(toast, /not available on this server/i);
});