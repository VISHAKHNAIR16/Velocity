/**
 * Quotations page: create estimates, convert to invoices.
 * Requires config.js, api.js, ui.js.
 * Security rule: user-supplied text is only ever written with textContent / .value,
 * never innerHTML.
 */
(() => {
  "use strict";

  const state = {
    page: 1,
    status: "",
    search: "",
    count: 0,
    parties: [],
    items: [],
  };

  let requestCounter = 0;
  let dirty = false;
  let lastFocused = null;

  const $ = (id) => document.getElementById(id);
  const tbody = $("quotations-tbody");
  const tableWrap = $("table-wrap");
  const emptyState = $("empty-state");
  const errorState = $("error-state");
  const loadingState = $("loading-state");
  const drawer = $("quotation-drawer");
  const drawerPanel = drawer.querySelector(".drawer-panel");
  const form = $("quotation-form");
  const submitBtn = $("quotation-submit-btn");
  const partySelect = form.elements["party"];

  function showTable() {
    loadingState.hidden = true;
    emptyState.hidden = true;
    errorState.hidden = true;
    tableWrap.hidden = false;
  }

  function setLoading(on) {
    if (on) {
      errorState.hidden = true;
      emptyState.hidden = true;
      tableWrap.hidden = true;
      loadingState.hidden = false;
      tableWrap.setAttribute("aria-busy", "true");
    } else {
      loadingState.hidden = true;
      tableWrap.removeAttribute("aria-busy");
    }
  }

  function showEmpty({ title, text, actionLabel, onAction }) {
    loadingState.hidden = true;
    errorState.hidden = true;
    tableWrap.hidden = true;
    tbody.replaceChildren();
    $("empty-title").textContent = title;
    $("empty-text").textContent = text;
    const action = $("empty-action");
    if (actionLabel) {
      action.textContent = actionLabel;
      action.classList.remove("hidden");
      action.onclick = onAction;
    } else {
      action.classList.add("hidden");
      action.onclick = null;
    }
    emptyState.hidden = false;
  }

  function showError(message) {
    loadingState.hidden = true;
    emptyState.hidden = true;
    tableWrap.hidden = true;
    tbody.replaceChildren();
    $("error-text").textContent = message || "Something went wrong. Please try again.";
    errorState.hidden = false;
  }

  async function loadParties() {
    try {
      const data = await apiRequest("/parties/?party_type=BOTH&is_active=true&ordering=name");
      state.parties = data.results || [];
      partySelect.replaceChildren(new Option("Select customer", ""));
      state.parties.forEach((p) => {
        if (p.party_type !== "CUSTOMER" && p.party_type !== "BOTH") return;
        partySelect.append(new Option(`${p.name} (${p.party_type})`, String(p.id)));
      });
    } catch (err) {
      showToast("Couldn't load customers.", "error");
    }
  }

  async function loadItems() {
    try {
      const data = await apiRequest("/items/?is_active=true&ordering=name");
      state.items = data.results || [];
    } catch (err) {
      showToast("Couldn't load items.", "error");
    }
  }

  async function loadQuotations() {
    const myRequest = ++requestCounter;
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: state.page, ordering: "-quotation_date" });
      if (state.status) params.set("status", state.status);
      if (state.search) params.set("search", state.search);
      const data = await apiRequest(`/quotations/?${params}`);
      if (myRequest !== requestCounter) return;
      state.count = data.count || 0;
      const results = data.results || [];
      if (results.length === 0) {
        showEmpty(emptyContent());
        return;
      }
      showTable();
      tbody.replaceChildren(...results.map(buildRow));
      renderSummary(results);
    } catch (err) {
      if (myRequest !== requestCounter) return;
      showError(err.message);
    } finally {
      if (myRequest === requestCounter) setLoading(false);
    }
  }

  function emptyContent() {
    const filtered = state.status || state.search;
    if (filtered) {
      return {
        title: "No matching quotations",
        text: "No quotations match your current filters.",
        actionLabel: "Clear filters",
        onAction: clearFilters,
      };
    }
    return {
      title: "No quotations yet",
      text: "Create your first quotation to start estimating.",
      actionLabel: "Create your first quotation",
      onAction: () => openDrawer(),
    };
  }

  function buildRow(q) {
    const tr = h("tr", { className: "transition-colors hover:bg-hover cursor-default" });
    const isExpired = q.is_expired;
    const statusLabel = q.status === "CONVERTED" ? "Converted" : q.status === "EXPIRED" ? "Expired" : q.status === "LOST" ? "Lost" : q.status === "SENT" ? "Sent" : "Draft";
    let badgeClass = "bg-blue-100 text-blue-700";
    if (q.status === "CONVERTED") badgeClass = "bg-green-100 text-green-700";
    if (q.status === "EXPIRED" || isExpired) badgeClass = "bg-red-100 text-red-700";
    tr.append(h("td", { className: "px-4 py-3 font-medium text-navy", textContent: q.display_number }));
    tr.append(h("td", { className: "px-4 py-3 font-medium", textContent: q.party_name }));
    tr.append(h("td", { className: "px-4 py-3 text-sm text-slate-500 hidden sm:table-cell", textContent: new Date(q.quotation_date).toLocaleDateString("en-IN") }));
    tr.append(h("td", { className: "px-4 py-3 text-sm text-slate-500 hidden sm:table-cell", textContent: new Date(q.valid_until).toLocaleDateString("en-IN") }));
    const badge = h("span", { className: `inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${badgeClass}`, textContent: statusLabel });
    tr.append(h("td", { className: "px-4 py-3 hidden sm:table-cell" }).appendChild(badge) && tbody);
    const amountEl = h("div", { className: "font-semibold tabular-nums text-navy", textContent: `₹${formatINR(Number(q.grand_total))}` });
    tr.append(h("td", { className: "px-4 py-3 text-right" }).appendChild(amountEl) && tbody);
    const actions = h("td", { className: "px-4 py-3 text-right w-[60px]" });
    if (q.status === "DRAFT" || q.status === "SENT") {
      const convertBtn = h("button", {
        type: "button",
        className: "text-xs font-medium text-blue-600 hover:text-blue-800",
        onclick: () => convertToInvoice(q.id),
        textContent: "Convert",
      });
      actions.append(convertBtn);
    }
    tr.append(actions);
    return tr;
  }

  function renderSummary(results) {
    let total = 0;
    results.forEach((q) => { total += Number(q.grand_total); });
    $("summary-total").textContent = `₹${formatINR(total)}`;
    $("summary-count").textContent = String(results.length);
  }

  function clearFilters() {
    state.status = "";
    state.search = "";
    state.page = 1;
    $("status-filter").value = "";
    $("search-input").value = "";
    loadQuotations();
  }

  function setPageLocked(locked) {
    document.body.classList.toggle("overflow-hidden", locked);
    document.body.classList.toggle("no-scroll", locked);
  }

  function openDrawerAnimation() {
    drawer.classList.remove("hidden");
    void drawerPanel.offsetWidth;
    drawerPanel.style.transform = "translateX(0)";
    setPageLocked(true);
  }

  function closeDrawerAnimation() {
    drawerPanel.style.transform = "translateX(100%)";
    setPageLocked(false);
    setTimeout(() => drawer.classList.add("hidden"), 200);
  }

  function resetForm() {
    form.reset();
    clearErrors(form);
    dirty = false;
    const today = new Date().toISOString().split("T")[0];
    const validUntil = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().split("T")[0];
    form.elements["quotation_date"].value = today;
    form.elements["valid_until"].value = validUntil;
  }

  async function openDrawer() {
    resetForm();
    $("quotation-drawer-title").textContent = "Create Quotation";
    $("quotation-drawer-subtitle").innerHTML = 'Fields marked <span class="text-danger">*</span> are required.';
    submitBtn.textContent = "Create Quotation";
    lastFocused = document.activeElement;
    await Promise.all([loadParties(), loadItems()]);
    openDrawerAnimation();
    setTimeout(() => form.elements["party"].focus(), 80);
  }

  function closeDrawer(force = false) {
    if (!force && dirty && !window.confirm("Discard your unsaved quotation?")) return;
    closeDrawerAnimation();
    dirty = false;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  function buildPayload() {
    const payload = {
      party: form.elements["party"].value,
      quotation_date: form.elements["quotation_date"].value,
      valid_until: form.elements["valid_until"].value,
      notes: form.elements["notes"].value.trim(),
      terms: form.elements["terms"].value.trim(),
      items: [],
    };
    document.querySelectorAll(".item-row").forEach((row) => {
      payload.items.push({
        item: row.querySelector("[name='item']").value || null,
        item_name: row.querySelector("[name='item_name']").value.trim(),
        item_type: row.querySelector("[name='item_type']").value,
        hsn_sac_code: row.querySelector("[name='hsn_sac_code']").value.trim(),
        unit: row.querySelector("[name='unit']").value,
        quantity: row.querySelector("[name='quantity']").value,
        unit_price: row.querySelector("[name='unit_price']").value,
        line_discount: row.querySelector("[name='line_discount']").value || "0.00",
        tax_rate: row.querySelector("[name='tax_rate']").value,
      });
    });
    return payload;
  }

  function validate(p) {
    const errors = {};
    const fail = (field, message) => { errors[field] = [message]; };
    if (!p.party) fail("party", "Select a customer.");
    if (!p.quotation_date) fail("quotation_date", "Select a date.");
    if (!p.valid_until) fail("valid_until", "Select a valid-until date.");
    if (p.items.length === 0) fail("items", "Add at least one line item.");
    else {
      p.items.forEach((line, i) => {
        if (!line.quantity || Number(line.quantity) <= 0) fail(`items.${i}.quantity`, "Quantity must be greater than zero.");
        if (!line.unit_price || Number(line.unit_price) < 0) fail(`items.${i}.unit_price`, "Enter a valid price.");
        if (!line.tax_rate) fail(`items.${i}.tax_rate`, "Select a tax rate.");
      });
    }
    return errors;
  }

  function markInvalidFields(errors) {
    form.querySelectorAll(".is-invalid").forEach((el) => { el.classList.remove("is-invalid"); el.removeAttribute("aria-invalid"); });
    Object.keys(errors).forEach((field) => {
      const input = form.elements[field];
      if (input && input.classList) { input.classList.add("is-invalid"); input.setAttribute("aria-invalid", "true"); }
    });
  }

  async function handleSubmit(event) {
    event.preventDefault();
    clearErrors(form);
    const payload = buildPayload();
    const errors = validate(payload);
    if (Object.keys(errors).length) {
      markInvalidFields(errors);
      showFieldErrors(form, { message: "Please fix the highlighted fields.", errors });
      const firstBad = form.querySelector(".is-invalid");
      if (firstBad) firstBad.focus();
      return;
    }
    setBusy(submitBtn, true, "Creating...");
    try {
      await apiRequest("/quotations/", { method: "POST", body: payload });
      closeDrawer(true);
      showToast("Quotation created successfully.");
      await loadQuotations();
    } catch (err) {
      markInvalidFields(err.errors || {});
      showFieldErrors(form, err);
    } finally {
      setBusy(submitBtn, false);
    }
  }

  async function convertToInvoice(quotationId) {
    setBusy(null, true, "Converting...");
    try {
      await apiRequest(`/quotations/${quotationId}/convert-to-invoice/`, { method: "POST" });
      showToast("Quotation converted to invoice.");
      await loadQuotations();
    } catch (err) {
      showToast(err.message || "Conversion failed.", "error");
    } finally {
      setBusy(null, false);
    }
  }

  function addItemRow() {
    const container = $("item-lines");
    const row = h("div", { className: "item-row grid sm:grid-cols-12 gap-2 mb-2 border-b pb-2" });
    const itemSelect = h("select", { name: "item", className: "input", required });
    itemSelect.append(new Option("Free-text line", ""));
    state.items.forEach((it) => itemSelect.append(new Option(`${it.name} · ${it.get_display_unit}`, String(it.id))));
    row.append(h("div", { className: "sm:col-span-3" }).appendChild(itemSelect) && h("div") && row);
    row.append(h("div", { className: "sm:col-span-2" }).appendChild(
      h("input", { name: "quantity", type: "text", inputmode: "decimal", data_decimal: true, placeholder: "Qty", className: "input", required }) && h("div")
    ) && row);
    row.append(h("div", { className: "sm:col-span-2" }).appendChild(
      h("input", { name: "unit_price", type: "text", inputmode: "decimal", data_decimal: true, placeholder: "Price", className: "input", required }) && h("div")
    ) && row);
    const taxSelect = h("select", { name: "tax_rate", className: "input", required });
    ["5.00", "12.00", "18.00", "28.00"].forEach((r) => taxSelect.append(new Option(`${r}%`, r)));
    row.append(h("div", { className: "sm:col-span-2" }).appendChild(taxSelect) && h("div") && row);
    row.append(h("div", { className: "sm:col-span-2" }).appendChild(
      h("input", { name: "line_discount", type: "text", inputmode: "decimal", data_decimal: true, placeholder: "Disc.", className: "input", value: "0.00" }) && h("div")
    ) && row);
    row.append(h("div", { className: "sm:col-span-1" }).appendChild(
      h("button", { type: "button", className: "btn btn-ghost text-danger", onclick: () => row.remove(), ariaLabel: "Remove line" }).appendChild(
        h("svg", { className: "w-4 h-4", fill: "none", stroke: "currentColor", viewBox: "0 0 24 24" }).appendChild(
          h("path", { stroke_linecap: "round", stroke_linejoin: "round", stroke_width: "2", d: "M6 18L18 6M6 6l12 12" })
        )
      ) && h("div")
    ) && row);
    container.append(row);
    dirty = true;
  }

  function handleFormInput(event) {
    dirty = true;
    const input = event.target;
    if (input.matches("[data-decimal]")) {
      input.value = input.value.replace(/[^\d.]/g, "").replace(/(\..*)\./g, "$1");
    }
    const slot = form.querySelector(`[data-error-for="${input.name}"]`);
    if (slot) slot.textContent = "";
    input.classList.remove("is-invalid");
    input.removeAttribute("aria-invalid");
  }

  function bindEvents() {
    $("create-quotation-btn").addEventListener("click", () => openDrawer());
    $("empty-action").addEventListener("click", () => openDrawer());
    $("retry-btn").addEventListener("click", loadQuotations);
    $("status-filter").addEventListener("change", (e) => { state.status = e.target.value; state.page = 1; loadQuotations(); });
    const searchInput = $("search-input");
    const applySearch = debounce(() => { state.search = searchInput.value.trim(); state.page = 1; loadQuotations(); }, 300);
    searchInput.addEventListener("input", applySearch);
    drawer.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", () => closeDrawer()));
    form.addEventListener("submit", handleSubmit);
    form.addEventListener("input", handleFormInput);
    $("add-item-line").addEventListener("click", addItemRow);
    document.addEventListener("keydown", (event) => { if (event.key !== "Escape") return; if (!drawer.classList.contains("hidden")) closeDrawer(); });
  }

  if (!requireLogin()) return;
  renderNav("quotations");
  bindEvents();
  loadQuotations();
})();
