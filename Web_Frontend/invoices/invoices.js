/**
 * Invoices list page: searchable / filterable table, status summary tiles,
 * and the issue / cancel / copy actions.
 *
 * Requires config.js, api.js, ui.js.
 * Security rule: user-supplied text is only ever written with textContent / .value,
 * never innerHTML.
 */
(() => {
  "use strict";

  const PAGE_SIZE = 20; // keep equal to PAGE_SIZE in the backend REST_FRAMEWORK settings

  const STATUS_LABELS = { DRAFT: "Draft", ISSUED: "Issued", CANCELLED: "Cancelled" };
  const STATUS_STYLES = {
    DRAFT: "badge-muted",
    ISSUED: "bg-ok-soft text-ok",
    CANCELLED: "bg-danger-soft text-danger",
  };

  // Static icons only (no user data), so innerHTML is safe for these.
  const ICON_EYE = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5s8.268 2.943 9.542 7c-1.274 4.057-5.065 7-9.542 7s-8.268-2.943-9.542-7z"/></svg>';
  const ICON_ISSUE = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>';
  const ICON_COPY = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16V8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H10a2 2 0 01-2-2z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2"/></svg>';
  const ICON_X = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';

  const state = {
    page: 1,
    search: "",
    status: "",
    supply_type: "",
    date_from: "",
    date_to: "",
  };

  let requestCounter = 0; // lets us ignore out-of-date responses
  let pendingCancel = null; // { id, number }

  // h(), debounce(), formatINR() and initials() are shared globals from ui.js.
  const $ = (id) => document.getElementById(id);

  const tbody = $("invoices-tbody");
  const tableCard = $("table-card");
  const loadingState = $("loading-state");
  const emptyState = $("empty-state");
  const errorState = $("error-state");
  const pagination = $("pagination");
  const activeFilters = $("active-filters");
  const summary = $("summary");
  const cancelDialog = $("cancel-dialog");
  const cancelReason = $("cancel-reason");
  const cancelConfirm = $("cancel-confirm");

  // ===================================================================
  // VIEW STATE
  // ===================================================================
  function showTable() {
    loadingState.hidden = true;
    emptyState.hidden = true;
    errorState.hidden = true;
    tableCard.classList.add("is-loading");
    pagination.hidden = false;
  }

  function showLoading() {
    loadingState.hidden = false;
    emptyState.hidden = true;
    errorState.hidden = true;
    tableCard.classList.add("is-loading");
    pagination.hidden = true;
  }

  function showEmpty() {
    loadingState.hidden = true;
    errorState.hidden = true;
    tableCard.classList.remove("is-loading");
    tbody.replaceChildren();
    emptyState.hidden = false;
    pagination.hidden = true;

    const filtered = state.search || state.status || state.supply_type
      || state.date_from || state.date_to;
    $("empty-title").textContent = filtered ? "No matching invoices" : "No invoices yet";
    $("empty-text").textContent = filtered
      ? "Try widening the filters or clearing the search."
      : "Create your first invoice to see it here.";
    $("empty-action").classList.toggle("hidden", Boolean(filtered));
  }

  function showError(message) {
    loadingState.hidden = true;
    emptyState.hidden = true;
    tableCard.classList.remove("is-loading");
    pagination.hidden = true;
    $("error-text").textContent = message;
    errorState.hidden = false;
  }

  // ===================================================================
  // SUMMARY TILES (server-computed, so they cover every filtered row)
  // ===================================================================
  function tile(label, value, sub) {
    const card = h("div", { className: "card py-4" },
      h("p", { className: "text-xs font-medium uppercase tracking-wide text-muted" }, label),
      h("p", { className: "mt-1 text-xl font-bold text-navy" }, value));
    if (sub) card.append(h("p", { className: "mt-0.5 text-xs text-muted" }, sub));
    return card;
  }

  function renderSummary(stats) {
    summary.replaceChildren(
      tile("Invoices", String(stats.count),
        `${stats.draft_count} draft · ${stats.issued_count} issued · ${stats.cancelled_count} cancelled`),
      tile("Taxable value", `₹${formatINR(Number(stats.taxable_total))}`),
      tile("Tax collected", `₹${formatINR(Number(stats.tax_total))}`,
        `Invoice total ₹${formatINR(Number(stats.grand_total))}`),
    );
  }

  async function loadSummary() {
    try {
      renderSummary(await apiRequest(`/invoices/stats/?${queryString()}`));
    } catch {
      summary.replaceChildren(); // tiles are a nicety; never block the table
    }
  }

  // ===================================================================
  // QUERY
  // ===================================================================
  function queryString(extra = {}) {
    const params = new URLSearchParams();
    const merged = { ...state, ...extra };
    Object.entries(merged).forEach(([key, value]) => {
      if (value !== "" && value != null && key !== "page") params.set(key, value);
    });
    params.set("page", extra.page || state.page);
    return params.toString();
  }

  // ===================================================================
  // ROWS
  // ===================================================================
  function iconButton(iconHtml, label, onClick, extraClass = "") {
    const button = h("button", {
      type: "button",
      className: `icon-btn ${extraClass}`.trim(),
      ariaLabel: label,
      title: label,
    });
    button.innerHTML = iconHtml; // static icon markup only
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      onClick();
    });
    return button;
  }

  function taxSummary(invoice) {
    if (Number(invoice.cgst_total) + Number(invoice.sgst_total) > 0) {
      return `CGST ${formatINR(Number(invoice.cgst_total))} + SGST ${formatINR(Number(invoice.sgst_total))}`;
    }
    if (Number(invoice.igst_total) > 0) return `IGST ${formatINR(Number(invoice.igst_total))}`;
    return "No tax";
  }

  function buildRow(invoice) {
    const row = h("tr", { className: "row-clickable" });
    const numberCell = h("td");
    numberCell.append(h("div", { className: "font-semibold text-navy" }, invoice.display_number));
    // A draft shows its created date instead of a number: the display name is
    // never "Draft #<pk>", which would leak other tenants' invoice volume.
    const sub = invoice.status === "DRAFT"
      ? `Saved ${new Date(invoice.created_at).toLocaleDateString("en-IN")}`
      : (invoice.supply_type === "INTER" ? "Inter-state" : "Intra-state");
    numberCell.append(h("div", { className: "cell-sub" }, sub));
    row.append(numberCell);

    row.append(h("td", { className: "whitespace-nowrap" },
      new Date(invoice.invoice_date).toLocaleDateString("en-IN", {
        day: "2-digit", month: "short", year: "numeric",
      })));

    const partyCell = h("td");
    partyCell.append(h("div", { className: "font-medium text-navy" }, invoice.party_name));
    row.append(partyCell);

    row.append(h("td", {}, h("span", { className: `badge ${STATUS_STYLES[invoice.status]}` },
      STATUS_LABELS[invoice.status] || invoice.status)));

    row.append(h("td", { className: "cell-sub" }, taxSummary(invoice)));
    row.append(h("td", { className: "num" }, `₹${formatINR(Number(invoice.taxable_total))}`));
    row.append(h("td", { className: "num amount" }, `₹${formatINR(Number(invoice.grand_total))}`));

    const actions = h("div", { className: "row-actions" });
    const open = () => { window.location.href = `/invoices/billing.html?id=${invoice.id}`; };
    actions.append(iconButton(ICON_EYE, invoice.status === "DRAFT" ? "Edit draft" : "View invoice", open));

    if (invoice.status === "DRAFT") {
      actions.append(iconButton(ICON_ISSUE, "Issue invoice", () => issueInvoice(invoice.id)));
      actions.append(iconButton(ICON_COPY, "Copy as new draft", () => copyInvoice(invoice.id)));
    } else if (invoice.status === "ISSUED") {
      actions.append(iconButton(ICON_X, "Cancel invoice", () => openCancelDialog(invoice), "danger"));
    }
    row.append(h("td", {}, actions));

    row.addEventListener("click", open);
    return row;
  }

  // ===================================================================
  // LOAD
  // ===================================================================
  async function loadInvoices() {
    const ticket = ++requestCounter;
    showLoading();
    try {
      const data = await apiRequest(`/invoices/?${queryString()}`);
      if (ticket !== requestCounter) return; // a newer request already won
      showTable();
      if (!data.results.length) { showEmpty(); return; }
      tbody.replaceChildren(...data.results.map(buildRow));
      renderPagination(data.count);
    } catch (err) {
      if (ticket !== requestCounter) return;
      showError(err.message);
    }
  }

  function refresh() {
    loadInvoices();
    loadSummary();
  }

  // ===================================================================
  // FILTERS
  // ===================================================================
  function renderActiveFilters() {
    const chips = [];
    const chip = (label, onRemove) => {
      const box = h("span", { className: "inline-flex items-center gap-1.5 rounded-full border border-line bg-tint px-3 py-1 text-xs text-navy" });
      box.append(document.createTextNode(label));
      const remove = h("button", { type: "button", className: "text-muted hover:text-brand", ariaLabel: `Remove filter ${label}` }, "×");
      remove.addEventListener("click", onRemove);
      box.append(remove);
      chips.push(box);
    };

    if (state.search) chip(`Search: ${state.search}`, () => { state.search = ""; $("search-input").value = ""; $("search-clear").classList.add("hidden"); state.page = 1; refresh(); });
    if (state.status) chip(`Status: ${STATUS_LABELS[state.status]}`, () => { state.status = ""; $("status-filter").value = ""; state.page = 1; refresh(); });
    if (state.supply_type) chip(`Supply: ${state.supply_type === "INTRA" ? "Intra-state" : "Inter-state"}`, () => { state.supply_type = ""; $("supply-filter").value = ""; state.page = 1; refresh(); });
    if (state.date_from) chip(`From ${state.date_from}`, () => { state.date_from = ""; $("from-date").value = ""; state.page = 1; refresh(); });
    if (state.date_to) chip(`To ${state.date_to}`, () => { state.date_to = ""; $("to-date").value = ""; state.page = 1; refresh(); });

    activeFilters.replaceChildren(...chips);
    activeFilters.classList.toggle("hidden", chips.length === 0);
  }

  // ===================================================================
  // PAGINATION
  // ===================================================================
  function renderPagination(count) {
    const pages = Math.ceil(count / PAGE_SIZE);
    const from = (state.page - 1) * PAGE_SIZE + 1;
    const to = Math.min(state.page * PAGE_SIZE, count);

    const info = h("span");
    info.textContent = `Showing ${from}–${to} of ${count}`;

    const pager = h("div", { className: "pager" });
    const addButton = (label, page, { disabled = false, active = false } = {}) => {
      const button = h("button", { type: "button", disabled, className: active ? "active" : "" }, label);
      button.addEventListener("click", () => {
        state.page = page;
        renderActiveFilters();
        refresh();
      });
      pager.append(button);
    };

    addButton("‹", state.page - 1, { disabled: state.page <= 1 });
    for (let p = 1; p <= pages; p += 1) {
      if (p === 1 || p === pages || Math.abs(p - state.page) <= 1) {
        addButton(String(p), p, { active: p === state.page });
      } else if (Math.abs(p - state.page) === 2) {
        pager.append(h("span", { className: "px-1 self-center" }, "…"));
      }
    }
    addButton("›", state.page + 1, { disabled: state.page >= pages });

    pagination.replaceChildren(info, pager);
    pagination.hidden = pages <= 1 && count === 0;
  }

  // ===================================================================
  // ACTIONS
  // ===================================================================
  async function issueInvoice(id) {
    try {
      const invoice = await apiRequest(`/invoices/${id}/issue/`, { method: "POST", body: {} });
      showToast(`Issued ${invoice.display_number}`);
      refresh();
    } catch (err) {
      // The server sends a machine-readable code; surface it so the user knows
      // which gate refused (missing HSN, incomplete profile, ...) rather than
      // just seeing a failure.
      const hints = {
        BUSINESS_PROFILE_INCOMPLETE: "Set your business state in Business profile first.",
        PARTY_STATE_MISSING: "Set this party's billing state first.",
        HSN_REQUIRED: "Every line needs an HSN/SAC code before issuing.",
        HSN_INVALID: "One of the HSN/SAC codes is not valid.",
        SERIES_EXHAUSTED: "This financial year's invoice series is full. Contact support.",
      };
      showToast(hints[err.code] || err.message, "error");
      refresh();
    }
  }

  async function copyInvoice(id) {
    try {
      const copy = await apiRequest(`/invoices/${id}/copy/`, { method: "POST", body: {} });
      showToast("Copied to a new draft.");
      window.location.href = `/invoices/billing.html?id=${copy.id}`;
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  function openCancelDialog(invoice) {
    pendingCancel = invoice;
    $("cancel-dialog-number").textContent = invoice.display_number;
    cancelReason.value = "";
    form_clearError("reason");
    cancelDialog.hidden = false;
    cancelReason.focus();
  }

  function closeCancelDialog() {
    cancelDialog.hidden = true;
    pendingCancel = null;
  }

  function form_clearError(field) {
    const el = cancelDialog.querySelector(`[data-error-for="${field}"]`);
    if (el) el.textContent = "";
  }

  async function confirmCancel() {
    if (!pendingCancel) return;
    const reason = cancelReason.value.trim();
    if (!reason) {
      form_clearError("reason");
      const el = cancelDialog.querySelector('[data-error-for="reason"]');
      el.textContent = "A reason is required - it is printed on the cancellation record.";
      cancelReason.focus();
      return;
    }
    setBusy(cancelConfirm, true, "Cancelling...");
    try {
      await apiRequest(`/invoices/${pendingCancel.id}/cancel/`, { method: "POST", body: { reason } });
      closeCancelDialog();
      showToast("Invoice cancelled. Its number is not reused.");
      refresh();
    } catch (err) {
      form_clearError("reason");
      const el = cancelDialog.querySelector('[data-error-for="reason"]');
      el.textContent = err.message;
    } finally {
      setBusy(cancelConfirm, false);
    }
  }

  // ===================================================================
  // EVENTS
  // ===================================================================
  function wireEvents() {
    const search = $("search-input");
    search.addEventListener("input", debounce(() => {
      state.search = search.value.trim();
      $("search-clear").classList.toggle("hidden", !state.search);
      state.page = 1;
      renderActiveFilters();
      refresh();
    }, 300));

    $("search-clear").addEventListener("click", () => {
      search.value = "";
      state.search = "";
      state.page = 1;
      $("search-clear").classList.add("hidden");
      renderActiveFilters();
      refresh();
      search.focus();
    });

    const bindSelect = (id, key) => $(id).addEventListener("change", (event) => {
      state[key] = event.target.value;
      state.page = 1;
      renderActiveFilters();
      refresh();
    });
    bindSelect("status-filter", "status");
    bindSelect("supply-filter", "supply_type");

    const bindDate = (id, key) => $(id).addEventListener("change", (event) => {
      state[key] = event.target.value;
      state.page = 1;
      renderActiveFilters();
      refresh();
    });
    bindDate("from-date", "date_from");
    bindDate("to-date", "date_to");

    $("retry-btn").addEventListener("click", refresh);
    cancelConfirm.addEventListener("click", confirmCancel);
    cancelDialog.querySelectorAll("[data-close]").forEach((el) => {
      el.addEventListener("click", closeCancelDialog);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !cancelDialog.hidden) closeCancelDialog();
    });
  }

  (async () => {
    if (!requireLogin()) return;
    renderNav("invoices");
    wireEvents();
    renderActiveFilters();
    refresh();
  })();
})();
