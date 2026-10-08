/**
 * Shared UI helpers. Requires api.js to be loaded first.
 * Security rule: user-supplied text is only ever written with textContent / .value,
 * never innerHTML.
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
 * Attribute support matters: without it a cell silently renders empty.
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

/** Clear all field errors and the top message inside a form. */
function clearErrors(form) {
  form.querySelectorAll("[data-error-for]").forEach((el) => { el.textContent = ""; });
  const box = form.querySelector("[data-form-message]");
  if (box) { box.textContent = ""; box.classList.add("hidden"); }
}

/** Show a message at the top of a form. kind: "error" | "success". */
function showFormMessage(form, text, kind = "error") {
  const box = form.querySelector("[data-form-message]");
  if (!box) return;
  box.textContent = text;
  box.className = "rounded p-3 text-sm " +
    (kind === "success" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700");
}

/** Put each backend field error under its input; anything else goes in the top message. */
function showFieldErrors(form, err) {
  const unmatched = [];
  for (const [field, msgs] of Object.entries(err.errors || {})) {
    const text = Array.isArray(msgs) ? msgs.join(" ") : String(msgs);
    const el = form.querySelector(`[data-error-for="${field}"]`);
    if (el) el.textContent = text; else unmatched.push(text);
  }
  showFormMessage(form, unmatched.length ? unmatched.join(" ") : err.message);
}

/** Disable a button while a request runs, so it cannot be double-submitted. */
function setBusy(button, busy, busyLabel = "Please wait...") {
  if (busy) { button.dataset.label = button.textContent; button.textContent = busyLabel; }
  else { button.textContent = button.dataset.label || button.textContent; }
  button.disabled = busy;
}

/** Send logged-out visitors to the login page. Returns false if redirected. */
function requireLogin() {
  if (!Auth.isLoggedIn()) { window.location.href = "/auth/login.html"; return false; }
  return true;
}

/** Send already-logged-in visitors away from login/register. */
function redirectIfLoggedIn() {
  if (Auth.isLoggedIn()) window.location.href = "/dashboard/";
}

/** Toast notification stack (bottom-right). */
function showToast(message, kind = "success") {
  let stack = document.querySelector(".toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    document.body.append(stack);
  }
  const toast = document.createElement("div");
  toast.className = `toast toast-${kind}`;
  toast.textContent = message;
  stack.append(toast);
  setTimeout(() => toast.remove(), 4000);
}

/** Draw the top navigation bar into <header id="nav" class="bg-navy">. Static markup only. */
function renderNav(active) {
  const link = (href, label, key) =>
    `<a href="${href}" class="px-3 py-2 rounded-lg text-sm font-medium transition-colors ${key === active ? "bg-white/10 text-white" : "text-white/80 hover:text-white hover:bg-white/5"}">${label}</a>`;
  document.getElementById("nav").innerHTML = `
    <div class="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
      <a href="/dashboard/" class="flex items-center gap-2 text-lg font-bold text-white" aria-label="Velocity Home">
        <span class="w-8 h-8 rounded-lg flex items-center justify-center text-sm" style="background: linear-gradient(135deg, var(--brand), var(--velocity));">V</span>
        Velocity
      </a>
      <nav class="flex items-center gap-1" aria-label="Main navigation">
        ${link("/dashboard/", "Dashboard", "dashboard")}
        ${link("/parties/index.html", "Parties", "parties")}
        ${link("/items/index.html", "Items", "items")}
        ${link("/invoices/index.html", "Invoices", "invoices")}
        ${link("/business/profile.html", "Business", "profile")}
        <button id="logout-btn" class="px-3 py-2 rounded-lg text-sm font-medium text-white/80 hover:text-white hover:bg-white/5 transition-colors" aria-label="Log out">Log out</button>
      </nav>
    </div>
    <div class="speed-bar"></div>`;
  document.getElementById("logout-btn").addEventListener("click", () => {
    Auth.clear();
    window.location.href = "/auth/login.html";
  });
}