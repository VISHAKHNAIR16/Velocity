/**
 * Shared UI helpers. Requires api.js to be loaded first.
 * Security rule: user-supplied text is only ever written with textContent / .value,
 * never innerHTML.
 */

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

/** Draw the top navigation bar into <header id="nav" class="bg-navy">. Static markup only. */
function renderNav(active) {
  const link = (href, label, key) =>
    `<a href="${href}" class="${key === active ? "font-semibold text-glow" : "text-white/80 hover:text-white"}">${label}</a>`;
  document.getElementById("nav").innerHTML = `
    <div class="mx-auto flex max-w-4xl items-center justify-between px-4 py-3">
      <a href="/dashboard/" class="text-lg font-bold text-white">Velocity</a>
      <nav class="flex items-center gap-5 text-sm">
        ${link("/dashboard/", "Dashboard", "dashboard")}
        ${link("/business/profile.html", "Business profile", "profile")}
        <button id="logout-btn" class="text-white/70 hover:text-white">Log out</button>
      </nav>
    </div>
    <div class="speed-bar"></div>`;
  document.getElementById("logout-btn").addEventListener("click", () => {
    Auth.clear();
    window.location.href = "/auth/login.html";
  });
}