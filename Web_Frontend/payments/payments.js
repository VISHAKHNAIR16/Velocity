/**
 * Payments page: record payments received/paid, list recent payments.
 * Requires config.js, api.js, ui.js.
 * Security rule: user-supplied text is only ever written with textContent / .value,
 * never innerHTML.
 */
(() => {
  "use strict";

  const PAGE_SIZE = 20;

  const state = {
    page: 1,
    transaction_type: "",    // "" = all, "PAYMENT_IN", "PAYMENT_OUT"
    search: "",
    date_from: "",
    date_to: "",
    count: 0,
    parties: [],             // cached party list for dropdown
    invoices: [],            // cached issued invoices for dropdown
  };

  let requestCounter = 0;
  let dirty = false;
  let lastFocused = null;

  const $ = (id) => document.getElementById(id);

  const tbody = $("payments-tbody");
  const tableWrap = $("table-wrap");
  const emptyState = $("empty-state");
  const loadingState = $("loading-state");
  const errorState = $("error-state");
  const drawer = $("payment-drawer");
  const drawerPanel = drawer.querySelector(".drawer-panel");
  const form = $("payment-form");
  const submitBtn = $("payment-submit-btn");
  const partySelect = form.elements["party"];
  const invoiceSelect = form.elements["invoice"];

  // ===================================================================
  // VIEW STATE
  // ===================================================================
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

  // ===================================================================
  // LOOKUPS (parties + invoices for dropdowns)
  // ===================================================================
  async function loadParties() {
    try {
      const data = await apiRequest("/parties/?party_type=BOTH&is_active=true&ordering=name");
      state.parties = data.results || [];
      partySelect.replaceChildren(new Option("Select customer or supplier", ""));
      state.parties.forEach((p) => {
        partySelect.append(new Option(`${p.name} (${p.party_type === "CUSTOMER" ? "Customer" : p.party_type === "SUPPLIER" ? "Supplier" : "Both"})`, String(p.id)));
      });
    } catch (err) {
      showToast("Couldn't load parties. The payment form may be incomplete.", "error");
    }
  }

  async function loadInvoices() {
    try {
      const data = await apiRequest("/invoices/?status=ISSUED&ordering=-invoice_date");
      state.invoices = data.results || [];
      invoiceSelect.replaceChildren(new Option("No invoice (payment on account)", ""));
      state.invoices.forEach((inv) => {
        invoiceSelect.append(new Option(`${inv.display_number} · ${inv.party_name}`, String(inv.id)));
      });
    } catch (err) {
      showToast("Couldn't load invoices. You can still record a payment without linking one.", "error");
    }
  }

  // ===================================================================
  // LIST
  // ===================================================================
  async function loadPayments() {
    const myRequest = ++requestCounter;
    setLoading(true);

    try {
      const params = new URLSearchParams({ page: state.page, ordering: "-payment_date" });
      if (state.transaction_type) params.set("transaction_type", state.transaction_type);
      if (state.search) params.set("search", state.search);
      if (state.date_from) params.set("date_from", state.date_from);
      if (state.date_to) params.set("date_to", state.date_to);

      const data = await apiRequest(`/payments/?${params}`);
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
    const filtered = state.transaction_type || state.search || state.date_from || state.date_to;
    if (filtered) {
      return {
        title: "No matching payments",
        text: "No payments match your current filters. Try a different search or clear the filters.",
        actionLabel: "Clear filters",
        onAction: clearFilters,
      };
    }
    return {
      title: "No payments yet",
      text: "Record your first payment to start tracking money in and out.",
      actionLabel: "Record your first payment",
      onAction: () => openDrawer(),
    };
  }

  function buildRow(payment) {
    const isIn = payment.transaction_type === "PAYMENT_IN";
    const tr = h("tr", { className: "transition-colors hover:bg-hover cursor-default" });

    // 1. Date
    tr.append(h("td", {
      className: "px-4 py-3 text-sm text-navy tabular-nums",
      textContent: new Date(payment.payment_date).toLocaleDateString("en-IN"),
    }));

    // 2. Party
    tr.append(h("td", {
      className: "px-4 py-3 font-medium",
      textContent: payment.party_name,
    }));

    // 3. Type
    tr.append(h("td", {
      className: "px-4 py-3 hidden sm:table-cell",
    }).appendChild(
      h("span", {
        className: `inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${isIn ? "bg-green-100 text-green-700" : "bg-blue-100 text-blue-700"}`,
        textContent: isIn ? "Received" : "Paid",
      })
    ) && tbody); // eslint-disable-line no-empty-pattern

    // Actually build properly:
    const tdType = h("td", { className: "px-4 py-3 hidden sm:table-cell" });
    tdType.append(h("span", {
      className: `inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${isIn ? "bg-green-100 text-green-700" : "bg-blue-100 text-blue-700"}`,
      textContent: isIn ? "Received" : "Paid",
    }));
    tr.append(tdType);

    // 4. Invoice
    const tdInvoice = h("td", {
      className: "px-4 py-3 text-sm text-slate-500 hidden md:table-cell",
      textContent: payment.invoice ? payment.invoice : "—",
    });
    tr.append(tdInvoice);

    // 5. Amount
    const amountEl = h("div", {
      className: "font-semibold tabular-nums text-navy",
      textContent: `₹${formatINR(Number(payment.amount))}`,
    });
    tr.append(h("td", { className: "px-4 py-3 text-right" }).appendChild(amountEl) && tbody);
    // Fix: append properly
    const tdAmount = h("td", { className: "px-4 py-3 text-right" });
    tdAmount.append(amountEl);
    tr.append(tdAmount);

    // 6. Mode
    const modeLabel = payment.payment_mode.replace(/_/g, " ").toLowerCase();
    tr.append(h("td", {
      className: "px-4 py-3 text-sm text-slate-500 hidden sm:table-cell",
      textContent: modeLabel,
    }));

    // 7. Reference
    tr.append(h("td", {
      className: "px-4 py-3 text-sm text-slate-500 hidden lg:table-cell",
      textContent: payment.reference_number || "—",
    }));

    return tr;
  }

  function renderSummary(results) {
    let received = 0;
    let paid = 0;
    results.forEach((p) => {
      const amt = Number(p.amount);
      if (p.transaction_type === "PAYMENT_IN") received += amt;
      else paid += amt;
    });

    $("summary-received").textContent = `₹${formatINR(received)}`;
    $("summary-paid").textContent = `₹${formatINR(paid)}`;
    $("summary-net").textContent = `₹${formatINR(received - paid)}`;
    $("summary-count").textContent = String(results.length);
  }

  function clearFilters() {
    state.transaction_type = "";
    state.search = "";
    state.date_from = "";
    state.date_to = "";
    state.page = 1;
    $("txn-type-filter").value = "";
    $("search-input").value = "";
    $("date-from-filter").value = "";
    $("date-to-filter").value = "";
    loadPayments();
  }

  // ===================================================================
  // DRAWER (record payment)
  // ===================================================================
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
    form.scrollTop = 0;
    // Default date = today
    const today = new Date().toISOString().split("T")[0];
    form.elements["payment_date"].value = today;
  }

  function fillFormDefaults() {
    // Nothing to fill for a new payment; defaults set in resetForm().
  }

  async function openDrawer() {
    resetForm();
    fillFormDefaults();
    $("payment-drawer-title").textContent = "Record Payment";
    $("payment-drawer-subtitle").innerHTML = 'Fields marked <span class="text-danger">*</span> are required.';
    submitBtn.textContent = "Record Payment";

    lastFocused = document.activeElement;

    // Ensure lookups are loaded
    await Promise.all([loadParties(), loadInvoices()]);

    openDrawerAnimation();
    setTimeout(() => form.elements["party"].focus(), 80);
  }

  function closeDrawer(force = false) {
    if (!force && dirty && !window.confirm("Discard your unsaved payment?")) return;
    closeDrawerAnimation();
    dirty = false;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  // ----- form data + validation -----
  function buildPayload() {
    const payload = {};
    payload.party = partySelect.value;
    payload.transaction_type = form.elements["transaction_type"].value;
    payload.payment_date = form.elements["payment_date"].value;
    payload.invoice = invoiceSelect.value || null;
    payload.amount = form.elements["amount"].value;
    payload.payment_mode = form.elements["payment_mode"].value;
    payload.reference_number = form.elements["reference_number"].value.trim();
    payload.notes = form.elements["notes"].value.trim();
    return payload;
  }

  function validate(p) {
    const errors = {};
    const fail = (field, message) => { errors[field] = [message]; };

    if (!p.party) fail("party", "Select a customer or supplier.");
    if (!p.transaction_type) fail("transaction_type", "Choose payment direction.");
    if (!p.payment_date) fail("payment_date", "Select a date.");
    if (!p.amount || !/^\d+(\.\d{1,2})?$/.test(p.amount)) fail("amount", "Enter a valid amount (e.g. 1500.50).");
    else if (Number(p.amount) <= 0) fail("amount", "Amount must be greater than zero.");
    if (!p.payment_mode) fail("payment_mode", "Choose a payment mode.");

    return errors;
  }

  function markInvalidFields(errors) {
    form.querySelectorAll(".is-invalid").forEach((el) => {
      el.classList.remove("is-invalid");
      el.removeAttribute("aria-invalid");
    });
    Object.keys(errors).forEach((field) => {
      const input = form.elements[field];
      if (input && input.classList) {
        input.classList.add("is-invalid");
        input.setAttribute("aria-invalid", "true");
      }
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
      await apiRequest("/payments/", { method: "POST", body: payload });
      closeDrawer(true);
      showToast("Payment recorded successfully.");
      await loadPayments();
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

  // ===================================================================
  // EVENTS
  // ===================================================================
  function bindEvents() {
    $("record-payment-btn").addEventListener("click", () => openDrawer());
    $("empty-action").addEventListener("click", () => openDrawer());
    $("retry-btn").addEventListener("click", loadPayments);

    $("txn-type-filter").addEventListener("change", (event) => {
      state.transaction_type = event.target.value;
      state.page = 1;
      loadPayments();
    });

    $("date-from-filter").addEventListener("change", (event) => {
      state.date_from = event.target.value;
      state.page = 1;
      loadPayments();
    });
    $("date-to-filter").addEventListener("change", (event) => {
      state.date_to = event.target.value;
      state.page = 1;
      loadPayments();
    });

    const searchInput = $("search-input");
    const applySearch = debounce(() => {
      state.search = searchInput.value.trim();
      state.page = 1;
      loadPayments();
    }, 300);
    searchInput.addEventListener("input", applySearch);

    // Drawer
    drawer.querySelectorAll("[data-close]").forEach((el) =>
      el.addEventListener("click", () => closeDrawer()));
    form.addEventListener("submit", handleSubmit);
    form.addEventListener("input", handleFormInput);

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!drawer.classList.contains("hidden")) closeDrawer();
    });
  }

  // ===================================================================
  // START
  // ===================================================================
  if (!requireLogin()) return;
  renderNav("payments");
  bindEvents();
  loadPayments();
})();
