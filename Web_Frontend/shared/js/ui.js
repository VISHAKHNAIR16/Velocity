/**
 * Shared UI helpers (v2 revamp). Requires api.js to be loaded first.
 * Security rule: user-supplied text is only ever written with textContent / .value,
 * never innerHTML. innerHTML below is used for STATIC markup only.
 */

// ===================================================================
// DOM helpers (shared by every page: parties, items, invoices, ...)
// ===================================================================

/**
 * Tiny hyperscript helper.
 *   h("div")                              -> <div></div>
 *   h("td", { className: "px-4" })        -> <td class="px-4"></td>
 *   h("td", "text")                       -> <td>text</td>
 *   h("div", childNode, [more, children])
 *
 * NOTE: an options object must be the SECOND argument, before any children.
 */
function h(tag, ...children) {
  const el = document.createElement(tag);

  if (children.length && isProps(children[0])) {
    applyProps(el, children.shift());
  }

  for (const child of children) {
    if (child == null || child === false) continue;
    if (Array.isArray(child)) {
      child.forEach((c) => { if (c != null && c !== false) el.append(c); });
    } else if (child instanceof Node) {
      el.append(child);
    } else {
      el.append(document.createTextNode(String(child)));
    }
  }
  return el;
}

/** True for a plain options object (not a DOM node, array, or primitive). */
function isProps(value) {
  return typeof value === "object"
    && value !== null
    && !(value instanceof Node)
    && !Array.isArray(value);
}

/** Apply an options object to an element (className, textContent, attrs, events). */
function applyProps(el, props) {
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;

    if (key === "className" || key === "class") {
      el.setAttribute("class", value);
    } else if (key === "textContent") {
      el.textContent = value;
    } else if (key === "style" && typeof value === "object") {
      Object.assign(el.style, value);
    } else if (key === "dataset" && typeof value === "object") {
      Object.assign(el.dataset, value);
    } else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "ariaLabel") {
      el.setAttribute("aria-label", value);
    } else if (value === true) {
      el.setAttribute(key, "");
    } else {
      el.setAttribute(key, String(value));
    }
  }
}

/** Format a number as Indian-style currency digits, e.g. 1,23,456.78 (no symbol). */
function formatINR(n) {
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

/** Debounce: run fn only after `wait` ms have passed without a new call. */
function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

/** Two-letter initials for an avatar, e.g. "Sunrise Traders" -> "ST". */
function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  const first = parts[0] ? parts[0][0] : "?";
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase();
}

// ===================================================================
// Form helpers
// ===================================================================

/** Clear all field errors, invalid highlights and the top message inside a form. */
function clearErrors(form) {
  form.querySelectorAll("[data-error-for]").forEach((el) => { el.textContent = ""; });
  form.querySelectorAll(".is-invalid").forEach((el) => el.classList.remove("is-invalid"));
  const box = form.querySelector("[data-form-message]");
  if (box) { box.textContent = ""; box.className = "hidden"; }
}

/** Show a message at the top of a form. kind: "error" | "success". */
function showFormMessage(form, text, kind = "error") {
  const box = form.querySelector("[data-form-message]");
  if (!box) return;
  box.textContent = text;
  box.className = "alert " + (kind === "success" ? "alert-success" : "alert-error");
  box.setAttribute("role", kind === "success" ? "status" : "alert");
}

/** Put each backend field error under its input; anything else goes in the top message. */
function showFieldErrors(form, err) {
  const unmatched = [];
  for (const [field, msgs] of Object.entries(err.errors || {})) {
    const text = Array.isArray(msgs) ? msgs.join(" ") : String(msgs);
    const el = form.querySelector(`[data-error-for="${field}"]`);
    if (el) {
      el.textContent = text;
      const input = form.elements[field];
      if (input && input.classList) input.classList.add("is-invalid");
    } else {
      unmatched.push(text);
    }
  }
  showFormMessage(form, unmatched.length ? unmatched.join(" ") : err.message);
}

/** Remembers a button's original children so setBusy can restore icons too. */
const _busyOriginal = new WeakMap();

/** Disable a button while a request runs, so it cannot be double-submitted. */
function setBusy(button, busy, busyLabel = "Please wait...") {
  if (busy) {
    _busyOriginal.set(button, Array.from(button.childNodes));
    button.textContent = busyLabel;
  } else if (_busyOriginal.has(button)) {
    button.replaceChildren(..._busyOriginal.get(button));
    _busyOriginal.delete(button);
  }
  button.disabled = busy;
  button.setAttribute("aria-busy", busy ? "true" : "false");
}

// ===================================================================
// Session helpers
// ===================================================================

/** Send logged-out visitors to the login page. Returns false if redirected. */
function requireLogin() {
  if (!Auth.isLoggedIn()) { window.location.href = "/auth/login.html"; return false; }
  return true;
}

/** Send already-logged-in visitors away from login/register. */
function redirectIfLoggedIn() {
  if (Auth.isLoggedIn()) window.location.href = "/dashboard/";
}

// ===================================================================
// Feedback + page effects
// ===================================================================

/** Toast notification stack (bottom-right). kind: success | error | warning. */
function showToast(message, kind = "success") {
  let stack = document.querySelector(".toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    stack.setAttribute("aria-live", "polite");
    document.body.append(stack);
  }
  const toast = document.createElement("div");
  toast.className = `toast toast-${kind}`;
  toast.textContent = message;
  stack.append(toast);
  setTimeout(() => toast.remove(), 4000);
}

/**
 * Fade elements in as they scroll into view.
 * Mark elements with class "reveal" (optionally style="--d:2" for a stagger step).
 * Without JS the elements stay visible because theme.css only hides them under html.js.
 */
function initReveal() {
  const items = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) { items.forEach((el) => el.classList.add("in")); return; }
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) { entry.target.classList.add("in"); observer.unobserve(entry.target); }
    });
  }, { threshold: 0.12 });
  items.forEach((el) => observer.observe(el));
}

// ===================================================================
// Top navigation (app pages)
// ===================================================================

/** Menu entries: [href, label, key]. Add a new page here once and every page gets it. */
const NAV_ITEMS = [
  ["/dashboard/", "Dashboard", "dashboard"],
  ["/payments/index.html", "Payments", "payments"],
  ["/parties/index.html", "Parties", "parties"],
  ["/items/index.html", "Items", "items"],
  ["/invoices/index.html", "Invoices", "invoices"],
  ["/purchases/index.html", "Purchases", "purchases"],
  ["/expenses/index.html", "Expenses", "expenses"],
  ["/quotations/index.html", "Quotations", "quotations"],
  ["/gstr/index.html", "GSTR Reports", "gstr"],
  ["/business/profile.html", "Business", "profile"],
];

/**
 * Draw the light, sticky top bar into <header id="nav">.
 * Static markup only (no user data). On screens narrower than 1280px the links
 * collapse behind a menu button.
 */
function renderNav(active) {
  const header = document.getElementById("nav");
  header.className = "app-nav"; // overrides any old "bg-navy" class on the header

  const links = NAV_ITEMS.map(([href, label, key]) =>
    `<a href="${href}" class="nav-link${key === active ? " active" : ""}"${key === active ? ' aria-current="page"' : ""}>${label}</a>`
  ).join("");

  header.innerHTML = `
    <div class="app-nav-inner">
      <a href="/dashboard/" class="brand" aria-label="Velocity home">
        <span class="brand-mark">V</span> Velocity
      </a>
      <button type="button" id="nav-toggle" class="icon-btn nav-toggle" aria-label="Open menu" aria-expanded="false" aria-controls="nav-links">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
      </button>
      <nav id="nav-links" class="nav-links" aria-label="Main navigation">
        ${links}
        <button type="button" id="logout-btn" class="nav-link" aria-label="Log out">Log out</button>
      </nav>
    </div>
    <div class="speed-bar"></div>`;

  const toggle = document.getElementById("nav-toggle");
  const panel = document.getElementById("nav-links");
  toggle.addEventListener("click", () => {
    const open = panel.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  document.getElementById("logout-btn").addEventListener("click", () => {
    Auth.clear();
    window.location.href = "/auth/login.html";
  });
}