/**
 * Parties page: directory list, filters, add/edit drawer, soft delete / restore.
 * Requires config.js, api.js, ui.js.
 * Security rule: user-supplied text is only ever written with textContent / .value,
 * never innerHTML.
 */
(() => {
  "use strict";

  const PAGE_SIZE = 20; // keep equal to PAGE_SIZE in the backend REST_FRAMEWORK settings

  // Form fields copied between the API payload and the drawer's inputs.
  const FIELDS = [
    "name", "party_type", "mobile", "email", "gstin", "pan", "state_code",
    "billing_address", "billing_city", "billing_pincode",
    "shipping_address", "shipping_city", "shipping_pincode",
    "opening_balance", "balance_type",
  ];

  const TYPE_LABELS = { CUSTOMER: "Customer", SUPPLIER: "Supplier", BOTH: "Both" };
  // Tailwind classes for the type pill. Colours are duplicated from theme.css so the
  // pill reads correctly even if the stylesheet loads late.
  const TYPE_STYLES = {
    CUSTOMER: "bg-cyan-soft text-cyan-800",
    SUPPLIER: "bg-brand-soft text-blue-800",
    BOTH: "bg-slate-200 text-slate-700",
  };

  // Static icons only (no user data), so innerHTML is safe for these.
  const ICON_EDIT = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>';
  const ICON_TRASH = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>';
  const ICON_RESTORE = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>';

  const state = {
    page: 1,
    type: "",             // "" = all, "CUSTOMER", "SUPPLIER", "BOTH"
    search: "",
    status: "active",     // "active" | "inactive"
    ordering: "name",
    count: 0,
    editingId: null,
    pendingDelete: null,  // { id, name }
  };

  let requestCounter = 0;  // lets us ignore out-of-date responses
  let lastGstin = "";      // keeps state / PAN in step with the GSTIN
  let dirty = false;       // unsaved changes in the drawer
  let lastFocused = null;  // element to refocus when the drawer closes
  let statesLoaded = false;

  // -------------------------------------------------------------------
  // Local helpers
  // NOTE: h(), debounce(), formatINR() and initials() are shared globals
  // from ui.js so every page uses one identical implementation.
  // -------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);

  // Cached element references
  const tbody = $("parties-tbody");
  const tableCard = $("table-card");
  const tableWrap = $("table-wrap");
  const emptyState = $("empty-state");
  const loadingState = $("loading-state");
  const errorState = $("error-state");
  const pagination = $("pagination");
  const activeFilters = $("active-filters");
  const drawer = $("drawer");
  const drawerPanel = drawer.querySelector(".drawer-panel");
  const form = $("party-form");
  const submitBtn = $("submit-btn");
  const stateSelect = form.elements["state_code"];
  const confirmWrap = $("confirm-dialog");

  // ===================================================================
  // VIEW STATE (loading / empty / error / table)
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
      pagination.hidden = true;
      loadingState.hidden = false;
      tableCard.setAttribute("aria-busy", "true");
    } else {
      loadingState.hidden = true;
      tableCard.setAttribute("aria-busy", "false");
    }
  }

  /** Friendly "nothing here" panel. Never looks like an error. */
  function showEmpty({ title, text, actionLabel, onAction }) {
    loadingState.hidden = true;
    errorState.hidden = true;
    tableWrap.hidden = true;
    pagination.hidden = true;
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

  /** Real failure panel (network / server). Kept visually distinct from empty. */
  function showError(message) {
    loadingState.hidden = true;
    emptyState.hidden = true;
    tableWrap.hidden = true;
    pagination.hidden = true;
    tbody.replaceChildren();
    $("error-text").textContent = message || "Something went wrong. Please try again.";
    errorState.hidden = false;
  }

  // ===================================================================
  // LIST
  // ===================================================================
  async function loadParties() {
    const myRequest = ++requestCounter;
    setLoading(true);

    try {
      const params = new URLSearchParams({ page: state.page, ordering: state.ordering });
      if (state.type) params.set("party_type", state.type);
      if (state.search) params.set("search", state.search);
      if (state.status === "inactive") params.set("is_active", "false");

      const data = await apiRequest(`/parties/?${params}`);
      if (myRequest !== requestCounter) return; // a newer request replaced this one

      state.count = data.count || 0;
      const results = data.results || [];

      if (results.length === 0) {
        showEmpty(emptyContent());
        renderActiveFilters();
        return;
      }

      showTable();
      tbody.replaceChildren(...results.map(buildRow));
      renderPagination(results.length);
      renderActiveFilters();
    } catch (err) {
      if (myRequest !== requestCounter) return;
      // Requested page no longer exists (e.g. last row on last page was deleted).
      if (err.status === 404 && state.page > 1) {
        state.page = 1;
        return loadParties();
      }
      showError(err.message);
    } finally {
      if (myRequest === requestCounter) setLoading(false);
    }
  }

  /** Decide which friendly empty message fits the current filters. */
  function emptyContent() {
    const filtered = state.type || state.search || state.status !== "active";
    if (filtered) {
      return {
        title: "No matching parties",
        text: "No parties match your current filters. Try a different search or clear the filters.",
        actionLabel: "Clear filters",
        onAction: clearFilters,
      };
    }
    if (state.status === "inactive") {
      return {
        title: "No deleted parties",
        text: "Parties you delete will be listed here, so you can restore them later.",
      };
    }
    return {
      title: "No parties yet",
      text: "Add your first customer or supplier to start creating invoices.",
      actionLabel: "Add your first party",
      onAction: () => openDrawer(),
    };
  }

  // ----- row rendering -----
  function buildRow(party) {
    const tr = h("tr", {
      className: "transition-colors hover:bg-hover " + (party.is_active ? "cursor-pointer" : "bg-slate-50/60"),
    });
    if (party.is_active) tr.addEventListener("click", () => openDrawer(party.id));

    // 1. Party (avatar + name + email)
    const tdParty = h("td", { className: "px-4 py-3" });
    const partyWrap = h("div", { className: "flex items-center gap-3" });
    const avatar = h("span", {
      className: "inline-flex flex-none items-center justify-center w-10 h-10 rounded-full bg-brand-soft text-brand text-sm font-bold",
      textContent: initials(party.name),
    });
    const info = h("div", { className: "min-w-0" });
    const nameButton = h("button", {
      type: "button",
      className: "font-semibold text-navy hover:text-brand text-left truncate block max-w-[16rem]",
      textContent: party.name,
    });
    info.append(nameButton);
    if (party.email) {
      info.append(h("div", { className: "text-xs text-muted truncate max-w-[16rem]", textContent: party.email }));
    }
    partyWrap.append(avatar, info);
    tdParty.append(partyWrap);

    // 2. Type pill
    const tdType = h("td", { className: "px-4 py-3 hidden sm:table-cell" });
    tdType.append(h("span", {
      className: `inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold ${TYPE_STYLES[party.party_type] || "bg-slate-200 text-slate-700"}`,
      textContent: TYPE_LABELS[party.party_type] || party.party_type,
    }));
    if (!party.is_active) {
      tdType.append(" ");
      tdType.append(h("span", {
        className: "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-warn-soft text-warn",
        textContent: "Deleted",
      }));
    }

    // 3. Mobile
    const tdMobile = h("td", { className: "px-4 py-3 text-sm text-navy hidden md:table-cell tabular-nums", textContent: party.mobile || "—" });

    // 4. GSTIN
    const tdGstin = h("td", { className: "px-4 py-3 hidden lg:table-cell" });
    if (party.gstin) {
      tdGstin.append(h("span", { className: "mono text-brand", textContent: party.gstin }));
    } else {
      tdGstin.append(h("span", {
        className: "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600",
        textContent: "Unregistered",
      }));
    }

    // 5. State
    const tdState = h("td", {
      className: "px-4 py-3 text-sm text-navy hidden lg:table-cell",
      textContent: party.display_state || party.state_code || "—",
    });

    // 6. Opening balance
    const tdBalance = h("td", { className: "px-4 py-3 text-right" });
    const amount = Number(party.opening_balance);
    if (!Number.isFinite(amount) || amount === 0) {
      tdBalance.append(h("span", { className: "text-sm text-muted", textContent: "—" }));
    } else {
      const toCollect = party.balance_type === "CREDIT"; // CREDIT = party owes us
      const amountEl = h("div", {
        className: `font-semibold tabular-nums ${toCollect ? "text-ok" : "text-warn"}`,
        textContent: `₹${formatINR(amount)}`,
      });
      const subEl = h("div", {
        className: "text-xs text-muted mt-0.5",
        textContent: toCollect ? "To collect" : "To pay",
      });
      tdBalance.append(amountEl, subEl);
    }

    // 7. Actions — deliberately spaced out (gap-2 + hit area) to avoid misclicks.
    const tdActions = h("td", { className: "px-4 py-3" });
    const actions = h("div", { className: "flex items-center justify-end gap-2" });
    if (party.is_active) {
      actions.append(
        iconButton(ICON_EDIT, `Edit ${party.name}`, () => openDrawer(party.id),
          "p-2 rounded-lg text-muted hover:text-brand hover:bg-brand-soft"),
        iconButton(ICON_TRASH, `Delete ${party.name}`, () => openConfirm(party),
          "p-2 rounded-lg text-muted hover:text-danger hover:bg-danger-soft"),
      );
    } else {
      const restore = h("button", {
        type: "button",
        className: "inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold text-brand bg-brand-soft hover:bg-blue-100 transition-colors",
      });
      restore.innerHTML = ICON_RESTORE; // static icon markup only
      restore.append("Restore");
      restore.addEventListener("click", (event) => { event.stopPropagation(); restoreParty(party); });
      actions.append(restore);
    }
    tdActions.append(actions);

    tr.append(tdParty, tdType, tdMobile, tdGstin, tdState, tdBalance, tdActions);
    return tr;
  }

  function iconButton(iconHtml, label, onClick, extraClass = "") {
    const button = h("button", { type: "button", className: extraClass.trim() });
    button.innerHTML = iconHtml; // static icon markup only
    button.title = label;
    button.setAttribute("aria-label", label);
    button.addEventListener("click", (event) => { event.stopPropagation(); onClick(); });
    return button;
  }

  // ----- active filter chips -----
  function renderActiveFilters() {
    const chips = [];

    if (state.type) {
      chips.push({
        label: `Type: ${TYPE_LABELS[state.type] || state.type}`,
        onRemove: () => { state.type = ""; $("type-filter").value = ""; },
      });
    }
    if (state.search) {
      chips.push({
        label: `Search: "${state.search}"`,
        onRemove: () => {
          state.search = "";
          $("search-input").value = "";
          $("search-clear").classList.add("hidden");
        },
      });
    }
    if (state.status !== "active") {
      chips.push({
        label: "Status: Deleted",
        onRemove: () => { state.status = "active"; $("status-filter").value = "active"; },
      });
    }

    activeFilters.replaceChildren();
    if (chips.length === 0) {
      activeFilters.classList.add("hidden");
      return;
    }

    chips.forEach((chip) => {
      const el = h("span", {
        className: "inline-flex items-center gap-1.5 pl-2.5 pr-1.5 py-1 rounded-full text-xs font-medium bg-brand-soft text-brand",
      });
      el.append(chip.label);
      const remove = h("button", {
        type: "button",
        className: "inline-flex items-center justify-center w-4 h-4 rounded-full hover:bg-blue-200",
        textContent: "×",
        title: `Remove filter: ${chip.label}`,
        ariaLabel: `Remove filter: ${chip.label}`,
      });
      remove.addEventListener("click", () => {
        chip.onRemove();
        state.page = 1;
        loadParties();
      });
      el.append(remove);
      activeFilters.append(el);
    });

    const clearAll = h("button", {
      type: "button",
      className: "px-2 py-1 rounded-full text-xs font-semibold text-muted hover:text-brand underline",
      textContent: "Clear all",
    });
    clearAll.addEventListener("click", clearFilters);
    activeFilters.append(clearAll);
    activeFilters.classList.remove("hidden");
  }

  function clearFilters() {
    state.type = "";
    state.search = "";
    state.status = "active";
    state.page = 1;
    $("search-input").value = "";
    $("search-clear").classList.add("hidden");
    $("status-filter").value = "active";
    $("type-filter").value = "";
    loadParties();
  }

  // ----- pagination -----
  function renderPagination(shown) {
    if (state.count === 0) {
      pagination.hidden = true;
      return;
    }

    const from = (state.page - 1) * PAGE_SIZE + 1;
    $("showing-from").textContent = String(from);
    $("showing-to").textContent = String(from + shown - 1);
    $("total-count").textContent = String(state.count);
    pagination.hidden = false;

    const totalPages = Math.ceil(state.count / PAGE_SIZE);
    const controls = $("pagination-controls");
    controls.replaceChildren();
    if (totalPages <= 1) return;

    const addButton = (label, page, { disabled = false, active = false } = {}) => {
      const button = h("button", {
        type: "button",
        className: `inline-flex items-center justify-center min-w-[2rem] h-9 px-3 rounded-lg border text-sm font-medium transition-colors ${
          active
            ? "bg-brand border-brand text-white"
            : "bg-surface border-line text-navy hover:bg-hover"
        }`,
      });
      button.textContent = label;
      button.disabled = disabled;
      if (disabled) button.classList.add("opacity-40", "cursor-not-allowed");
      if (active) button.setAttribute("aria-current", "page");
      button.addEventListener("click", () => {
        if (state.page === page) return;
        state.page = page;
        loadParties();
        tableCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      return button;
    };

    controls.append(addButton("Prev", state.page - 1, { disabled: state.page === 1 }));

    // Window of page numbers around the current page.
    const end = Math.min(totalPages, Math.max(1, state.page - 1) + 2);
    const start = Math.max(1, end - 2);
    for (let page = start; page <= end; page++) {
      controls.append(addButton(String(page), page, { active: page === state.page }));
    }

    controls.append(addButton("Next", state.page + 1, { disabled: state.page === totalPages }));
  }

  // ===================================================================
  // DRAWER (add / edit)
  // ===================================================================
  function setPageLocked(locked) {
    document.body.classList.toggle("overflow-hidden", locked);
    document.body.classList.toggle("no-scroll", locked);
  }

  function openDrawerAnimation() {
    drawer.classList.remove("hidden");
    // Force a reflow so the transform transition actually runs.
    void drawerPanel.offsetWidth;
    drawerPanel.style.transform = "translateX(0)";
    setPageLocked(true);
  }

  function closeDrawerAnimation() {
    drawerPanel.style.transform = "translateX(100%)";
    setPageLocked(false);
    setTimeout(() => drawer.classList.add("hidden"), 200);
  }

  async function ensureStates() {
    if (statesLoaded) return;
    try {
      const states = await apiRequest("/meta/states/");
      states.sort((a, b) => a.name.localeCompare(b.name));
      states.forEach((s) => stateSelect.append(new Option(`${s.name} (${s.code})`, s.code)));
      statesLoaded = true;
    } catch {
      showToast("Couldn't load the list of states. Please try again.", "error");
    }
  }

  function resetForm() {
    form.reset();               // restores defaults (type = Customer, same-as-billing ticked)
    clearErrors(form);
    markInvalidFields({});
    lastGstin = "";
    dirty = false;
    syncShipping();
    form.scrollTop = 0;
  }

  function fillForm(party) {
    FIELDS.forEach((field) => { form.elements[field].value = party[field] ?? ""; });
    if (Number(party.opening_balance) === 0) form.elements["opening_balance"].value = "";
    const hasShipping = party.shipping_address || party.shipping_city || party.shipping_pincode;
    form.elements["same_as_billing"].checked = !hasShipping;
    lastGstin = party.gstin || "";
    syncShipping();
    dirty = false;
  }

  async function openDrawer(partyId = null) {
    state.editingId = partyId;
    resetForm();
    $("drawer-title").textContent = partyId ? "Edit Party" : "Add Party";
    $("drawer-subtitle").innerHTML = partyId
      ? "Update the details below."
      : 'Fields marked <span class="text-danger">*</span> are required.';
    submitBtn.textContent = partyId ? "Save Changes" : "Add Party";

    lastFocused = document.activeElement;
    openDrawerAnimation();

    if (!partyId) {
      ensureStates();
      setTimeout(() => form.elements["name"].focus(), 80);
      return;
    }

    // Editing: the list payload doesn't carry addresses, so fetch the full record.
    form.setAttribute("aria-busy", "true");
    form.classList.add("opacity-50", "pointer-events-none");
    submitBtn.disabled = true;
    try {
      const [party] = await Promise.all([apiRequest(`/parties/${partyId}/`), ensureStates()]);
      if (state.editingId !== partyId) return; // drawer was closed meanwhile
      fillForm(party);
      form.elements["name"].focus();
    } catch (err) {
      if (state.editingId === partyId) {
        closeDrawer(true);
        showToast(err.message, "error");
      }
    } finally {
      form.classList.remove("opacity-50", "pointer-events-none");
      form.removeAttribute("aria-busy");
      submitBtn.disabled = false;
    }
  }

  function closeDrawer(force = false) {
    if (!force && dirty && !window.confirm("Discard your unsaved changes?")) return;
    closeDrawerAnimation();
    state.editingId = null;
    dirty = false;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  function syncShipping() {
    const fields = $("shipping-fields");
    fields.classList.toggle("hidden", form.elements["same_as_billing"].checked);
  }

  // ----- form data + validation -----
  function buildPayload() {
    const payload = {};
    FIELDS.forEach((field) => { payload[field] = form.elements[field].value.trim(); });
    payload.gstin = payload.gstin.toUpperCase();
    payload.pan = payload.pan.toUpperCase();
    if (form.elements["same_as_billing"].checked) {
      // Blank shipping = "use billing address" (the backend falls back automatically).
      payload.shipping_address = "";
      payload.shipping_city = "";
      payload.shipping_pincode = "";
    }
    if (payload.opening_balance === "") payload.opening_balance = "0";
    return payload;
  }

  /** Quick checks for instant feedback. The backend validates everything again. */
  function validate(p) {
    const errors = {};
    const fail = (field, message) => { errors[field] = [message]; };

    if (!p.name) fail("name", "Enter the party's name.");
    if (!p.party_type) fail("party_type", "Choose a party type.");
    if (!/^[6-9]\d{9}$/.test(p.mobile)) fail("mobile", "Enter a 10-digit mobile number starting with 6, 7, 8 or 9.");
    if (p.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(p.email)) fail("email", "Enter a valid email address.");
    if (!p.state_code) fail("state_code", "Select the party's state.");

    if (p.gstin && !/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/.test(p.gstin)) {
      fail("gstin", "Enter a valid 15-character GSTIN, e.g. 36ABCCS2942R1ZR.");
    } else if (p.gstin && p.state_code && p.gstin.slice(0, 2) !== p.state_code) {
      fail("gstin", `This GSTIN starts with ${p.gstin.slice(0, 2)}, which doesn't match the selected state.`);
    }
    if (p.pan && !/^[A-Z]{5}\d{4}[A-Z]$/.test(p.pan)) {
      fail("pan", "Enter a valid 10-character PAN, e.g. ABCCS2942R.");
    } else if (p.pan && p.gstin && !errors.gstin && p.gstin.slice(2, 12) !== p.pan) {
      fail("pan", "PAN must match characters 3-12 of the GSTIN.");
    }

    for (const field of ["billing_pincode", "shipping_pincode"]) {
      if (p[field] && !/^[1-9]\d{5}$/.test(p[field])) fail(field, "Enter a valid 6-digit pincode.");
    }
    if (!/^\d{1,10}(\.\d{1,2})?$/.test(p.opening_balance)) {
      fail("opening_balance", "Enter an amount with up to 2 decimals, e.g. 1500.50.");
    }
    return errors;
  }

  /** Highlight the inputs that failed so the user can see what to fix. */
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

    const isEdit = state.editingId !== null;
    setBusy(submitBtn, true, "Saving...");
    try {
      await apiRequest(isEdit ? `/parties/${state.editingId}/` : "/parties/", {
        method: isEdit ? "PATCH" : "POST",
        body: payload,
      });
      closeDrawer(true);
      showToast(isEdit ? "Party updated successfully." : "Party added successfully.");
      await loadParties();
    } catch (err) {
      markInvalidFields(err.errors || {});
      showFieldErrors(form, err); // e.g. "A party with this GSTIN already exists..."
    } finally {
      setBusy(submitBtn, false);
    }
  }

  /** Keep state and PAN in step with the GSTIN, without overwriting deliberate typing. */
  function handleGstinInput() {
    const gstinInput = form.elements["gstin"];
    const value = gstinInput.value.toUpperCase().replace(/[^0-9A-Z]/g, "");
    gstinInput.value = value;
    const previous = lastGstin;
    lastGstin = value;

    const stateInput = form.elements["state_code"];
    const panInput = form.elements["pan"];
    const stateFromGstin = value.slice(0, 2);

    if (/^\d{2}$/.test(stateFromGstin)
        && (stateInput.value === "" || stateInput.value === previous.slice(0, 2))
        && [...stateInput.options].some((o) => o.value === stateFromGstin)) {
      stateInput.value = stateFromGstin;
    }
    if (value.length >= 12 && (panInput.value === "" || panInput.value === previous.slice(2, 12))) {
      panInput.value = value.slice(2, 12);
    }
  }

  function handleFormInput(event) {
    dirty = true;
    const input = event.target;
    if (input.matches("[data-digits]")) input.value = input.value.replace(/\D/g, "");
    if (input.matches("[data-decimal]")) {
      input.value = input.value.replace(/[^\d.]/g, "").replace(/(\..*)\./g, "$1");
    }
    if (input.name === "gstin") handleGstinInput();
    if (input.name === "pan") input.value = input.value.toUpperCase();

    // Clear this field's error as soon as the user edits it.
    const slot = form.querySelector(`[data-error-for="${input.name}"]`);
    if (slot) slot.textContent = "";
    input.classList.remove("is-invalid");
    input.removeAttribute("aria-invalid");
  }

  // ===================================================================
  // DELETE / RESTORE
  // ===================================================================
  function openConfirm(party) {
    state.pendingDelete = { id: party.id, name: party.name };
    $("confirm-title").textContent = `Delete "${party.name}"?`;
    $("confirm-text").textContent =
      "This party will be hidden from lists and dropdowns, but existing invoices keep their history. "
      + "You can restore it any time from the Deleted filter.";
    confirmWrap.classList.remove("hidden");
    $("confirm-ok").focus();
  }

  function closeConfirm() {
    confirmWrap.classList.add("hidden");
    state.pendingDelete = null;
  }

  async function confirmDelete() {
    const target = state.pendingDelete;
    if (!target) return;
    const okBtn = $("confirm-ok");
    setBusy(okBtn, true, "Deleting...");
    try {
      await apiRequest(`/parties/${target.id}/`, { method: "DELETE" });
      closeConfirm();
      showToast(`"${target.name}" was deleted.`);
      await loadParties();
    } catch (err) {
      closeConfirm();
      showToast(err.message, "error");
    } finally {
      setBusy(okBtn, false);
    }
  }

  async function restoreParty(party) {
    try {
      await apiRequest(`/parties/${party.id}/restore/`, { method: "POST" });
      showToast(`"${party.name}" was restored.`);
      await loadParties();
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  // ===================================================================
  // EVENTS
  // ===================================================================
  function bindEvents() {
    $("add-party-btn").addEventListener("click", () => openDrawer());
    $("empty-action").addEventListener("click", () => openDrawer());
    $("retry-btn").addEventListener("click", loadParties);

    $("type-filter").addEventListener("change", (event) => {
      state.type = event.target.value;
      state.page = 1;
      loadParties();
    });

    $("status-filter").addEventListener("change", (event) => {
      state.status = event.target.value;
      state.page = 1;
      loadParties();
    });

    $("sort-select").addEventListener("change", (event) => {
      state.ordering = event.target.value;
      state.page = 1;
      loadParties();
    });

    const searchInput = $("search-input");
    const applySearch = debounce(() => {
      state.search = searchInput.value.trim();
      state.page = 1;
      loadParties();
    }, 300);
    searchInput.addEventListener("input", () => {
      $("search-clear").classList.toggle("hidden", searchInput.value === "");
      applySearch();
    });
    $("search-clear").addEventListener("click", () => {
      searchInput.value = "";
      $("search-clear").classList.add("hidden");
      state.search = "";
      state.page = 1;
      loadParties();
      searchInput.focus();
    });

    // Drawer
    drawer.querySelectorAll("[data-close]").forEach((el) =>
      el.addEventListener("click", () => closeDrawer()));
    form.addEventListener("submit", handleSubmit);
    form.addEventListener("input", handleFormInput);
    form.elements["same_as_billing"].addEventListener("change", () => {
      dirty = true;
      syncShipping();
    });

    // Confirm dialog
    $("confirm-cancel").addEventListener("click", closeConfirm);
    $("confirm-backdrop").addEventListener("click", closeConfirm);
    $("confirm-ok").addEventListener("click", confirmDelete);

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!confirmWrap.classList.contains("hidden")) closeConfirm();
      else if (!drawer.classList.contains("hidden")) closeDrawer();
    });
  }

  // ===================================================================
  // START
  // ===================================================================
  if (!requireLogin()) return;
  renderNav("parties");
  bindEvents();
  ensureStates();
  loadParties();
})();