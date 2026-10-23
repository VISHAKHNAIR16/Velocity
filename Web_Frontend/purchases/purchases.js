/**
 * Purchases page: record supplier bills, list purchases.
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
    date_from: "",
    date_to: "",
    count: 0,
    parties: [],
    items: [],
  };

  let requestCounter = 0;
  let dirty = false;
  let lastFocused = null;

  const $ = (id) => document.getElementById(id);
  const tbody = $("purchases-tbody");
  const tableWrap = $("table-wrap");
  const emptyState = $("empty-state");
  const errorState = $("error-state");
  const drawer = $("purchase-drawer");
  const drawerPanel = drawer.querySelector(".drawer-panel");
  const form = $("purchase-form");
  const submitBtn = $("purchase-submit-btn");
  const partySelect = form.elements["party"];

  function showTable() {
    loadingState.hidden = true;
    emptyState.hidden = true;
    errorState.hidden = true;
    tableWrap.hidden = false;
  }

  const loadingState = $("loading-state");

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
      partySelect.replaceChildren(new Option("Select supplier", ""));
      state.parties.forEach((p) => {
        if (p.party_type !== "SUPPLIER" && p.party_type !== "BOTH") return;
        partySelect.append(new Option(`${p.name} (${p.party_type})`, String(p.id)));
      });
    } catch (err) {
      showToast("Couldn't load suppliers. The purchase form may be incomplete.", "error");
    }
  }

  async function loadPurchases() {
    const myRequest = ++requestCounter;
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: state.page, ordering: "-purchase_date" });
      if (state.status) params.set("status", state.status);
      if (state.search) params.set("search", state.search);
      if (state.date_from) params.set("date_from", state.date_from);
      if (state.date_to) params.set("date_to", state.date_to);
      const data = await apiRequest(`/purchases/?${params}`);
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
    const filtered = state.status || state.search || state.date_from || state.date_to;
    if (filtered) {
      return {
        title: "No matching purchases",
        text: "No purchases match your current filters. Try a different search or clear the filters.",
        actionLabel: "Clear filters",
        onAction: clearFilters,
      };
    }
    return {
      title: "No purchases yet",
      text: "Record your first supplier bill to start tracking what you owe.",
      actionLabel: "Record your first purchase",
      onAction: () => openDrawer(),
    };
  }

  function buildRow(p) {
    const tr = h("tr", { className: "transition-colors hover:bg-hover cursor-default" });
    tr.append(h("td", { className: "px-4 py-3 text-sm text-navy tabular-nums", textContent: new Date(p.purchase_date).toLocaleDateString("en-IN") }));
    tr.append(h("td", { className: "px-4 py-3 font-medium", textContent: p.party_name }));
    tr.append(h("td", { className: "px-4 py-3 text-sm text-slate-500", textContent: p.bill_number || "—" }));
    const statusLabel = p.status === "ISSUED" ? "Issued" : p.status === "DRAFT" ? "Draft" : "Cancelled";
    const statusBadge = h("span", { className: "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold bg-blue-100 text-blue-700", textContent: statusLabel });
    tr.append(h("td", { className: "px-4 py-3 hidden sm:table-cell" }).appendChild(statusBadge) && tbody);
    const amountEl = h("div", { className: "font-semibold tabular-nums text-navy", textContent: `₹${formatINR(Number(p.grand_total))}` });
    tr.append(h("td", { className: "px-4 py-3 text-right" }).appendChild(amountEl) && tbody);
    tr.append(h("td", { className: "px-4 py-3 text-sm text-slate-500 hidden sm:table-cell", textContent: p.supply_type === "INTRA" ? "Intra-state" : "Inter-state" }));
    return tr;
  }

  function renderSummary(results) {
    let total = 0;
    results.forEach((p) => { total += Number(p.grand_total); });
    $("summary-total").textContent = `₹${formatINR(total)}`;
    $("summary-count").textContent = String(results.length);
  }

  function clearFilters() {
    state.status = "";
    state.search = "";
    state.date_from = "";
    state.date_to = "";
    state.page = 1;
    $("status-filter").value = "";
    $("search-input").value = "";
    $("date-from-filter").value = "";
    $("date-to-filter").value = "";
    loadPurchases();
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
    form.elements["purchase_date"].value = today;
  }

  async function openDrawer() {
    resetForm();
    $("purchase-drawer-title").textContent = "Record Purchase";
    $("purchase-drawer-subtitle").innerHTML = 'Fields marked <span class="text-danger">*</span> are required.';
    submitBtn.textContent = "Record Purchase";
    lastFocused = document.activeElement;
    await loadParties();
    openDrawerAnimation();
    setTimeout(() => form.elements["party"].focus(), 80);
  }

  function closeDrawer(force = false) {
    if (!force && dirty && !window.confirm("Discard your unsaved purchase?")) return;
    closeDrawerAnimation();
    dirty = false;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  function buildPayload() {
    const payload = {
      party: form.elements["party"].value,
      purchase_date: form.elements["purchase_date"].value,
      bill_number: form.elements["bill_number"].value.trim(),
      place_of_supply: form.elements["place_of_supply"].value,
      supply_type: form.elements["supply_type"].value,
      prices_include_tax: form.elements["prices_include_tax"].checked,
      notes: form.elements["notes"].value.trim(),
      items: [],
    };
    const itemRows = form.querySelectorAll(".item-row");
    itemRows.forEach((row) => {
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
    if (!p.party) fail("party", "Select a supplier.");
    if (!p.purchase_date) fail("purchase_date", "Select a date.");
    if (!p.place_of_supply) fail("place_of_supply", "Select place of supply.");
    if (!p.supply_type) fail("supply_type", "Select supply type.");
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
    setBusy(submitBtn, true, "Recording...");
    try {
      await apiRequest("/purchases/", { method: "POST", body: payload });
      closeDrawer(true);
      showToast("Purchase recorded successfully.");
      await loadPurchases();
    } catch (err) {
      markInvalidFields(err.errors || {});
      showFieldErrors(form, err);
    } finally {
      setBusy(submitBtn, false);
    }
  }

  function addItemRow() {
    const container = $("item-lines");
    const row = h("div", { className: "item-row grid sm:grid-cols-12 gap-2 mb-2 border-b pb-2" });
    row.append(h("div", { className: "sm:col-span-3" }).appendChild(
      h("select", { name: "item", className: "input", required }).appendChild(h("option", { value: "", textContent: "Select item" })) && h("div")
    ) && row);
    state.items.forEach((it) => {
      row.querySelector("select[name='item']").append(new Option(`${it.name} · ${it.get_display_unit}`, String(it.id)));
    });
    row.append(h("div", { className: "sm:col-span-2" }).appendChild(
      h("input", { name: "quantity", type: "text", inputmode: "decimal", data_decimal: true, placeholder: "Qty", className: "input", required }) && h("div")
    ) && row);
    row.append(h("div", { className: "sm:col-span-2" }).appendChild(
      h("input", { name: "unit_price", type: "text", inputmode: "decimal", data_decimal: true, placeholder: "Price", className: "input", required }) && h("div")
    ) && row);
    row.append(h("div", { className: "sm:col-span-2" }).appendChild(
      h("select", { name: "tax_rate", className: "input", required }) && h("div")
    ));
    row.querySelector("select[name='tax_rate']").append(new Option("5%", "5.00"));
    row.querySelector("select[name='tax_rate']").append(new Option("12%", "12.00"));
    row.querySelector("select[name='tax_rate']").append(new Option("18%", "18.00"));
    row.querySelector("select[name='tax_rate']").append(new Option("28%", "28.00"));
    row.append(h("div", { className: "sm:col-span-2" }).appendChild(
      h("input", { name: "line_discount", type: "text", inputmode: "decimal", data_decimal: true, placeholder: "Disc.", className: "input", value: "0.00" }) && h("div")
    ) && row);
    row.append(h("div", { className: "sm:col-span-1" }).appendChild(
      h("button", { type: "button", className: "btn btn-ghost text-danger", onclick: () => row.remove(), ariaLabel: "Remove line" }).appendChild(h("svg", { className: "w-4 h-4", fill: "none", stroke: "currentColor", viewBox: "0 0 24 24" }).appendChild(h("path", { stroke_linecap: "round", stroke_linejoin: "round", stroke_width: "2", d: "M6 18L18 6M6 6l12 12" }))) && h("div")
    ) && row);
    container.append(row);
    dirty = true;
  }

  function bindEvents() {
    $("record-purchase-btn").addEventListener("click", () => openDrawer());
    $("empty-action").addEventListener("click", () => openDrawer());
    $("retry-btn").addEventListener("click", loadPurchases);
    $("status-filter").addEventListener("change", (e) => { state.status = e.target.value; state.page = 1; loadPurchases(); });
    $("date-from-filter").addEventListener("change", (e) => { state.date_from = e.target.value; state.page = 1; loadPurchases(); });
    $("date-to-filter").addEventListener("change", (e) => { state.date_to = e.target.value; state.page = 1; loadPurchases(); });
    const searchInput = $("search-input");
    const applySearch = debounce(() => { state.search = searchInput.value.trim(); state.page = 1; loadPurchases(); }, 300);
    searchInput.addEventListener("input", applySearch);
    drawer.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", () => closeDrawer()));
    form.addEventListener("submit", handleSubmit);
    form.addEventListener("input", handleFormInput);
    $("add-item-line").addEventListener("click", addItemRow);
    document.addEventListener("keydown", (event) => { if (event.key !== "Escape") return; if (!drawer.classList.contains("hidden")) closeDrawer(); });
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

  if (!requireLogin()) return;
  renderNav("purchases");
  loadItems();
  bindEvents();
  loadPurchases();

  async function loadItems() {
    try {
      const data = await apiRequest("/items/?item_type=PRODUCT&is_active=true&ordering=name");
      state.items = data.results || [];
    } catch (err) {
      showToast("Couldn't load items. You can still add free-text lines.", "error");
    }
  }
})();
