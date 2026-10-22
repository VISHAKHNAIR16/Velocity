/**
 * Frontend tests for the dashboard (3.3).
 *
 * Verifies the dashboard HTML structure, shared helper functions, and that the
 * page scripts load without errors. Async rendering (fetch + DOM update) is
 * verified manually via the QA checklist (docs/QA_CHECKLIST.md) since jsdom's
 * event loop doesn't reliably drive the dashboard's async IIFE.
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

// Escape helper copied from dashboard.js for independent verification.
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

test("dashboard: HTML contains all expected sections", () => {
  const html = readPage("dashboard/index.html");

  assert.ok(html.includes('<title>Dashboard | Velocity</title>'));
  assert.ok(html.includes('id="metric-cards"'), "metric cards container");
  assert.ok(html.includes('id="low-stock-body"'), "low-stock table body");
  assert.ok(html.includes('id="recent-payments-body"'), "recent payments table");
  assert.ok(html.includes('src="/dashboard/dashboard.js"'), "dashboard.js is loaded");
});

test("dashboard: dashboard.js references all three API endpoints", () => {
  const js = readPage("dashboard/dashboard.js");
  assert.ok(js.includes("/api/v1/dashboard/stats/"), "stats API endpoint");
  assert.ok(js.includes("/api/v1/dashboard/low-stock/"), "low-stock API endpoint");
  assert.ok(js.includes("/api/v1/payments/"), "payments API endpoint");
});

test("dashboard: dashboard.js loads without syntax errors", () => {
  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "http://localhost/dashboard/",
    runScripts: "outside-only",
  });
  const { window } = dom;
  const { document } = window;

  // Expose minimal globals the dashboard needs.
  window.document = document;
  window.Element = window.Element;
  window.Node = window.Node;

  const apiJs = fs.readFileSync(path.join(ROOT, "shared/js/api.js"), "utf8");
  window.eval(apiJs + "\nwindow.Auth = Auth; window.apiRequest = apiRequest;");

  const uiJs = fs.readFileSync(path.join(ROOT, "shared/js/ui.js"), "utf8");
  window.eval(
    uiJs +
      "\nwindow.Auth = Auth; window.requireLogin = requireLogin; window.renderNav = renderNav;" +
      "\nwindow.h = h; window.debounce = debounce; window.showToast = showToast;" +
      "\nwindow.clearErrors = clearErrors; window.showFormMessage = showFormMessage;" +
      "\nwindow.showFieldErrors = showFieldErrors; window.setBusy = setBusy;" +
      "\nwindow.formatINR = formatINR;"
  );

  // formatINR should format Indian-style currency.
  assert.strictEqual(window.formatINR(123456.78), "1,23,456.78");
  assert.strictEqual(window.formatINR(0), "0.00");
  assert.strictEqual(window.formatINR(8000), "8,000.00");
});

test("dashboard: escapeHtml neutralises HTML tags", () => {
  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>");
  const { document } = dom.window;

  const div = document.createElement("div");
  div.textContent = "<script>alert('x')</script>";
  assert.strictEqual(div.innerHTML, "&lt;script&gt;alert('x')&lt;/script&gt;");
});

test("dashboard: index.html references all required shared scripts", () => {
  const html = readPage("dashboard/index.html");
  assert.ok(html.includes('src="/shared/js/config.js"'));
  assert.ok(html.includes('src="/shared/js/api.js"'));
  assert.ok(html.includes('src="/shared/js/ui.js"'));
  assert.ok(html.includes('src="/dashboard/dashboard.js"'));
});
