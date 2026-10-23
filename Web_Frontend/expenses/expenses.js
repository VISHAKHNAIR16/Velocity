/**
 * Expenses page: record operational expenses, list expenses.
 * Requires config.js, api.js, ui.js.
 * Security rule: user-supplied text is only ever written with textContent / .value,
 * never innerHTML.
 */
(() => {
  "use strict";

  const state = {
    page: 1,
    category: "",
    search: "",
    date_from: "",
    date_to: "",
    count: 0,
    categories: [],
  };

  let requestCounter = 0;
  let dirty = false;
  let lastFocused = null;

  const $ = (id) => document.getElementById(id);
  const tbody = $("expenses-tbody");
  const tableWrap = $("table-wrap");
  const emptyState = $("empty-state");
  const errorState = $("error-state");
  const loadingState = $("loading-state");
  const drawer = $("expense-drawer");
  const drawerPanel = drawer.querySelector(".drawer-panel");
  const form = $("expense-form");
  const submitBtn = $("expense-submit-btn");
  const categorySelect = form.elements["category"];

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

  async function loadCategories() {
    try {
      const data = await apiRequest("/expense-categories/?ordering=name");
      state.categories = data.results || [];
      categorySelect.replaceChildren(new Option("Select category", ""));
      state.categories.forEach((c) => categorySelect.append(new Option(c.name, String(c.id))));
      const filter = $("category-filter");
      filter.replaceChildren(new Option("All categories", ""));
      state.categories.forEach((c) => filter.append(new Option(c.name, String(c.id))));
    } catch (err) {
      showToast("Couldn't load expense categories.", "error");
    }
  }

  async function loadExpenses() {
    const myRequest = ++requestCounter;
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: state.page, ordering: "-expense_date" });
      if (state.category) params.set("category", state.category);
      if (state.search) params.set("search", state.search);
      if (state.date_from) params.set("date_from", state.date_from);
      if (state.date_to) params.set("date_to", state.date_to);
      const data = await apiRequest(`/expenses/?${params}`);
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
    const filtered = state.category || state.search || state.date_from || state.date_to;
    if (filtered) {
      return {
        title: "No matching expenses",
        text: "No expenses match your current filters.",
        actionLabel: "Clear filters",
        onAction: clearFilters,
      };
    }
    return {
      title: "No expenses yet",
      text: "Record your first expense to start tracking overheads.",
      actionLabel: "Record your first expense",
      onAction: () => openDrawer(),
    };
  }

  function buildRow(e) {
    const tr = h("tr", { className: "transition-colors hover:bg-hover cursor-default" });
    tr.append(h("td", { className: "px-4 py-3 text-sm text-navy tabular-nums", textContent: new Date(e.expense_date).toLocaleDateString("en-IN") }));
    tr.append(h("td", { className: "px-4 py-3 font-medium", textContent: e.category_name }));
    tr.append(h("td", { className: "px-4 py-3 text-sm text-slate-500 hidden sm:table-cell", textContent: e.payment_mode.replace(/_/g, " ").toLowerCase() }));
    const amountEl = h("div", { className: "font-semibold tabular-nums text-navy", textContent: `₹${formatINR(Number(e.amount))}` });
    tr.append(h("td", { className: "px-4 py-3 text-right" }).appendChild(amountEl) && tbody);
    const taxEl = h("div", { className: "font-semibold tabular-nums text-navy", textContent: `₹${formatINR(Number(e.tax_paid || 0))}` });
    tr.append(h("td", { className: "px-4 py-3 text-right" }).appendChild(taxEl) && tbody);
    tr.append(h("td", { className: "px-4 py-3 text-sm text-slate-500 hidden lg:table-cell", textContent: e.reference_number || "—" }));
    return tr;
  }

  function renderSummary(results) {
    let total = 0, tax = 0;
    results.forEach((e) => { total += Number(e.amount); tax += Number(e.tax_paid || 0); });
    $("summary-total").textContent = `₹${formatINR(total)}`;
    $("summary-tax").textContent = `₹${formatINR(tax)}`;
    $("summary-count").textContent = String(results.length);
  }

  function clearFilters() {
    state.category = "";
    state.search = "";
    state.date_from = "";
    state.date_to = "";
    state.page = 1;
    $("category-filter").value = "";
    $("search-input").value = "";
    $("date-from-filter").value = "";
    $("date-to-filter").value = "";
    loadExpenses();
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
    form.elements["expense_date"].value = today;
  }

  async function openDrawer() {
    resetForm();
    $("expense-drawer-title").textContent = "Record Expense";
    $("expense-drawer-subtitle").innerHTML = 'Fields marked <span class="text-danger">*</span> are required.';
    submitBtn.textContent = "Record Expense";
    lastFocused = document.activeElement;
    await loadCategories();
    openDrawerAnimation();
    setTimeout(() => form.elements["category"].focus(), 80);
  }

  function closeDrawer(force = false) {
    if (!force && dirty && !window.confirm("Discard your unsaved expense?")) return;
    closeDrawerAnimation();
    dirty = false;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  function buildPayload() {
    return {
      category: form.elements["category"].value,
      expense_date: form.elements["expense_date"].value,
      amount: form.elements["amount"].value,
      tax_paid: form.elements["tax_paid"].value || "0.00",
      payment_mode: form.elements["payment_mode"].value,
      reference_number: form.elements["reference_number"].value.trim(),
      notes: form.elements["notes"].value.trim(),
      receipt_url: form.elements["receipt_url"].value.trim(),
    };
  }

  function validate(p) {
    const errors = {};
    const fail = (field, message) => { errors[field] = [message]; };
    if (!p.category) fail("category", "Select a category.");
    if (!p.expense_date) fail("expense_date", "Select a date.");
    if (!p.amount || !/^\d+(\.\d{1,2})?$/.test(p.amount)) fail("amount", "Enter a valid amount.");
    else if (Number(p.amount) <= 0) fail("amount", "Amount must be greater than zero.");
    if (!p.payment_mode) fail("payment_mode", "Choose a payment mode.");
    if (p.tax_paid && !/^\d+(\.\d{1,2})?$/.test(p.tax_paid)) fail("tax_paid", "Enter a valid tax amount.");
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
      await apiRequest("/expenses/", { method: "POST", body: payload });
      closeDrawer(true);
      showToast("Expense recorded successfully.");
      await loadExpenses();
    } catch (err) {
      markInvalidFields(err.errors || {});
      showFieldErrors(form, err);
    } finally {
      setBusy(submitBtn, false);
    }
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
    $("record-expense-btn").addEventListener("click", () => openDrawer());
    $("empty-action").addEventListener("click", () => openDrawer());
    $("retry-btn").addEventListener("click", loadExpenses);
    $("category-filter").addEventListener("change", (e) => { state.category = e.target.value; state.page = 1; loadExpenses(); });
    $("date-from-filter").addEventListener("change", (e) => { state.date_from = e.target.value; state.page = 1; loadExpenses(); });
    $("date-to-filter").addEventListener("change", (e) => { state.date_to = e.target.value; state.page = 1; loadExpenses(); });
    const searchInput = $("search-input");
    const applySearch = debounce(() => { state.search = searchInput.value.trim(); state.page = 1; loadExpenses(); }, 300);
    searchInput.addEventListener("input", applySearch);
    drawer.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", () => closeDrawer()));
    form.addEventListener("submit", handleSubmit);
    form.addEventListener("input", handleFormInput);
    document.addEventListener("keydown", (event) => { if (event.key !== "Escape") return; if (!drawer.classList.contains("hidden")) closeDrawer(); });
  }

  if (!requireLogin()) return;
  renderNav("expenses");
  bindEvents();
  loadExpenses();
})();
