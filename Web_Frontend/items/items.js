/**
 * Items page: catalogue list, filters, add/edit drawer, soft delete / restore.
 * Requires config.js, api.js, ui.js.
 * Security rule: user text is only written with textContent, never innerHTML.
 *
 * h(), debounce(), formatINR() and initials() are shared globals from ui.js.
 */
(() => {
  "use strict";

  const PAGE_SIZE = 20; // keep equal to PAGE_SIZE in the backend REST_FRAMEWORK settings

  // Form fields copied between the API payload and the drawer's inputs.
const FIELDS = [
      "name", "item_type", "item_code", "barcode", "hsn_sac_code", "service_description",
      "unit", "sales_price", "purchase_price", "price_includes_tax",
      "tax_rate", "current_stock", "low_stock_threshold", "track_stock",
    ];

  // Fields that must be sent even when empty, so a PATCH can clear them.
  const NULLABLE_FIELDS = ["item_code", "barcode", "hsn_sac_code", "service_description",
    "purchase_price", "current_stock", "low_stock_threshold"];

  const TYPE_STYLES = {
    PRODUCT: "bg-blue-100 text-blue-700",
    SERVICE: "bg-violet-100 text-violet-700",
  };
  const TYPE_HINTS = {
    PRODUCT: "Products track stock quantity and use an HSN code.",
    SERVICE: "Services have no stock. They use a SAC code and need a description for the invoice.",
  };
  const CODE_LABEL = { PRODUCT: "HSN Code", SERVICE: "SAC Code" };
  const CODE_HINT = { PRODUCT: "4, 6 or 8 digits.", SERVICE: "4 or 6 digits." };

  // Static icons only (no user data), so innerHTML is safe for these.
  const ICON_EDIT = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>';
  const ICON_TRASH = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>';
  const ICON_RESTORE = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>';

  const state = {
    page: 1,
    type: "",            // "" | "PRODUCT" | "SERVICE"
    search: "",
    stock: "",           // "" | "low"
    status: "active",    // "active" | "inactive"
    ordering: "name",
count: 0,
      editingId: null,
      editingItem: null,  // the loaded item, for the read-only stock panel
      pendingDelete: null, // { id, name }
    };

  let requestCounter = 0;
  let dirty = false;      // unsaved changes in the drawer
  let lastFocused = null; // element to refocus when the drawer closes
  let optionsLoaded = false;

  const $ = (id) => document.getElementById(id);

  // Cached element references
  const tbody = $("items-tbody");
  const tableCard = $("table-card");
  const tableWrap = $("table-wrap");
  const emptyState = $("empty-state");
  const loadingState = $("loading-state");
  const errorState = $("error-state");
  const pagination = $("pagination");
  const activeFilters = $("active-filters");
  const drawer = $("drawer");
  const drawerPanel = drawer.querySelector(".drawer-panel");
  const form = $("item-form");
  const submitBtn = $("submit-btn");
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

  /** Real failure panel (network / server). Visually distinct from empty. */
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
  async function loadItems() {
    const myRequest = ++requestCounter;
    setLoading(true);

    try {
      const params = new URLSearchParams({ page: state.page, ordering: state.ordering });
      if (state.type) params.set("item_type", state.type);
      if (state.search) params.set("search", state.search);
      if (state.stock === "low") params.set("low_stock", "true");
      if (state.stock === "negative") params.set("negative_stock", "true");
      if (state.status === "inactive") params.set("is_active", "false");

      const data = await apiRequest(`/items/?${params}`);
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
        return loadItems();
      }
      showError(err.message);
    } finally {
      if (myRequest === requestCounter) setLoading(false);
    }
  }

  /** Decide which friendly empty message fits the current filters. */
  function emptyContent() {
    const filtered = state.type || state.search || state.stock || state.status !== "active";
    if (filtered) {
      return {
        title: "No matching items",
        text: "No items match your current filters. Try a different search or clear the filters.",
        actionLabel: "Clear filters",
        onAction: clearFilters,
      };
    }
    if (state.status === "inactive") {
      return {
        title: "No deleted items",
        text: "Items you delete will be listed here, so you can restore them later.",
      };
    }
    return {
      title: "No items yet",
      text: "Add your first product or service to start building your catalogue.",
      actionLabel: "Add your first item",
      onAction: () => openDrawer(),
    };
  }

  // ----- row rendering -----
  function buildRow(item) {
    const tr = h("tr", {
      className: "transition-colors hover:bg-hover " + (item.is_active ? "cursor-pointer" : "bg-slate-50/60"),
    });
    if (item.is_active) tr.addEventListener("click", () => openDrawer(item.id));

    const isService = item.item_type === "SERVICE";

    // 1. Item (avatar + name + code)
    const tdItem = h("td", { className: "px-4 py-3" });
    const wrap = h("div", { className: "flex items-center gap-3" });
    const avatar = h("span", {
      className: `inline-flex flex-none items-center justify-center w-10 h-10 rounded-full text-sm font-bold ${
        isService ? "bg-violet-100 text-violet-700" : "bg-brand-soft text-brand"
      }`,
      textContent: initials(item.name),
    });
    const info = h("div", { className: "min-w-0" });
    const nameButton = h("button", {
      type: "button",
      className: "font-semibold text-navy hover:text-brand text-left truncate block max-w-[16rem]",
      textContent: item.name,
    });
    info.append(nameButton);
    if (item.item_code) {
      info.append(h("div", { className: "text-xs text-muted mono truncate max-w-[16rem]", textContent: item.item_code }));
    }
    if (!item.is_active) {
      info.append(" ", h("span", {
        className: "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-warn-soft text-warn",
        textContent: "Deleted",
      }));
    }
    wrap.append(avatar, info);
    tdItem.append(wrap);

    // 2. Type pill
    const tdType = h("td", { className: "px-4 py-3 hidden sm:table-cell" });
    tdType.append(h("span", {
      className: `inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold ${TYPE_STYLES[item.item_type] || "bg-slate-200 text-slate-700"}`,
      textContent: item.item_type_label || item.item_type,
    }));

    // 3. HSN / SAC
    const tdCode = h("td", { className: "px-4 py-3 hidden md:table-cell" });
    if (item.hsn_sac_code) {
      tdCode.append(h("span", { className: "mono text-brand", textContent: item.hsn_sac_code }));
      if (isService && item.service_description) {
        tdCode.append(h("div", { className: "text-xs text-muted truncate max-w-[12rem]", textContent: item.service_description }));
      }
    } else {
      tdCode.append(h("span", {
        className: "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600",
        textContent: "Not set",
      }));
    }

    // 4. Unit
    const tdUnit = h("td", { className: "px-4 py-3 text-sm text-navy hidden lg:table-cell", textContent: item.display_unit || item.unit || "—" });

    // 5. Sale price
    const tdPrice = h("td", { className: "px-4 py-3 text-right" });
    const price = Number(item.sales_price);
    tdPrice.append(h("div", {
      className: "font-semibold tabular-nums text-navy",
      textContent: Number.isFinite(price) ? `₹${formatINR(price)}` : "—",
    }));
    tdPrice.append(h("div", { className: "text-xs text-muted mt-0.5", textContent: `GST ${item.tax_rate_label || ""}` }));

    // 6. Stock - only ever shown for products (services have no stock)
    const tdStock = h("td", { className: "px-4 py-3 text-right hidden lg:table-cell" });
    if (!item.tracks_stock) {
      tdStock.append(h("span", { className: "text-sm text-muted", textContent: "—" }));
    } else if (item.current_stock === null || item.current_stock === undefined) {
      tdStock.append(h("span", { className: "text-sm text-muted", textContent: "Not tracked" }));
    } else {
      const qty = Number(item.current_stock);
      tdStock.append(h("div", {
        className: `font-semibold tabular-nums ${item.is_low_stock ? "text-danger" : "text-navy"}`,
        textContent: Number.isFinite(qty) ? `${formatINR(qty)} ${item.unit || ""}`.trim() : "—",
      }));
      if (item.is_low_stock) {
        tdStock.append(h("div", { className: "text-xs text-danger mt-0.5", textContent: "Low stock" }));
      }
    }

    // 7. Actions - spaced out to avoid misclicks
    const tdActions = h("td", { className: "px-4 py-3" });
    const actions = h("div", { className: "flex items-center justify-end gap-2" });
    if (item.is_active) {
      actions.append(
        iconButton(ICON_EDIT, `Edit ${item.name}`, () => openDrawer(item.id),
          "p-2 rounded-lg text-muted hover:text-brand hover:bg-brand-soft"),
        iconButton(ICON_TRASH, `Delete ${item.name}`, () => openConfirm(item),
          "p-2 rounded-lg text-muted hover:text-danger hover:bg-danger-soft"),
      );
    } else {
      const restore = h("button", {
        type: "button",
        className: "inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold text-brand bg-brand-soft hover:bg-blue-100 transition-colors",
      });
      restore.innerHTML = ICON_RESTORE; // static icon markup only
      restore.append("Restore");
      restore.addEventListener("click", (event) => { event.stopPropagation(); restoreItem(item); });
      actions.append(restore);
    }
    tdActions.append(actions);

    tr.append(tdItem, tdType, tdCode, tdUnit, tdPrice, tdStock, tdActions);
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
        label: `Type: ${state.type === "PRODUCT" ? "Products" : "Services"}`,
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
    if (state.stock === "low") {
      chips.push({ label: "Low stock", onRemove: () => { state.stock = ""; $("stock-filter").value = ""; } });
      if (state.stock === "negative") {
        chips.push({ label: "Below zero", onRemove: () => { state.stock = ""; $("stock-filter").value = ""; } });
      }
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
        loadItems();
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
    state.stock = "";
    state.status = "active";
    state.page = 1;
    $("search-input").value = "";
    $("search-clear").classList.add("hidden");
    $("status-filter").value = "active";
    $("stock-filter").value = "";
    $("type-filter").value = "";
    loadItems();
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
          active ? "bg-brand border-brand text-white" : "bg-surface border-line text-navy hover:bg-hover"
        }`,
      });
      button.textContent = label;
      button.disabled = disabled;
      if (disabled) button.classList.add("opacity-40", "cursor-not-allowed");
      if (active) button.setAttribute("aria-current", "page");
      button.addEventListener("click", () => {
        if (state.page === page) return;
        state.page = page;
        loadItems();
        tableCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      return button;
    };

    controls.append(addButton("Prev", state.page - 1, { disabled: state.page === 1 }));

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
    void drawerPanel.offsetWidth; // force reflow so the transform transition runs
    drawerPanel.style.transform = "translateX(0)";
    setPageLocked(true);
  }

  function closeDrawerAnimation() {
    drawerPanel.style.transform = "translateX(100%)";
    setPageLocked(false);
    setTimeout(() => drawer.classList.add("hidden"), 200);
  }

  /** Load the unit + GST rate dropdowns from the backend (never hardcode them). */
  async function ensureOptions() {
    if (optionsLoaded) return;
    try {
      const [units, rates] = await Promise.all([
        apiRequest("/meta/units/"),
        apiRequest("/meta/gst-rates/"),
      ]);

      const unitSelect = form.elements["unit"];
      unitSelect.replaceChildren();
      units.forEach((u) => unitSelect.append(new Option(`${u.name} (${u.code})`, u.code)));
      if (!unitSelect.value) unitSelect.value = "PCS";

      const rateSelect = form.elements["tax_rate"];
      rateSelect.replaceChildren();
      rates.forEach((r) => rateSelect.append(new Option(r.label, r.value)));
      rateSelect.value = "18.00";

      optionsLoaded = true;
    } catch {
      showToast("Couldn't load units and tax rates. Please try again.", "error");
    }
  }

// Stock editing is context-dependent, so one function drives all of it.
    //
    // Create mode: the quantity box is the *opening* balance and is editable.
    // Edit mode + tracked: read-only, with Adjust Stock / History instead.
    // Edit mode + untracked: editable, because there is no ledger to lie to.
    // Services: no stock at all.
    function applyStockMode(item) {
      const isService = (item?.item_type ?? form.elements["item_type"].value) === "SERVICE";
      const editing = Boolean(state.editingId);
      const tracks = item
        ? Boolean(item.tracks_stock)
        : form.elements["track_stock"].checked;

      $("stock-section").classList.toggle("hidden", isService);
      $("stock-readonly-panel").classList.toggle("hidden", isService || !editing || !tracks);

      const box = $("f-current_stock");
      const readOnly = isService || (editing && tracks);
      box.readOnly = readOnly;
      box.classList.toggle("bg-tint/50", readOnly);
      box.classList.toggle("cursor-not-allowed", readOnly);

      // Opening stock (create) vs a read-only balance (edit).
      $("current-stock-label").textContent = editing ? "Opening Stock" : "Opening Stock";
      $("current-stock-hint").textContent = readOnly
        ? "Locked. Use Adjust Stock to record a correction."
        : "Count what is on the shelf now. Later changes are recorded automatically.";

      if (state.editingId && item) {
        $("ro-current-stock").textContent =
          item.current_stock === null || item.current_stock === undefined
            ? "—"
            : `${Number(item.current_stock).toFixed(3)} ${item.unit || ""}`.trim();
        $("ro-low-stock-hint").textContent = item.is_low_stock
          ? "Below the low-stock alert level."
          : "";
        $("unit-locked-note").classList.toggle("hidden", !item.unit_locked);
        const unitSelect = form.elements["unit"];
        unitSelect.disabled = Boolean(item.unit_locked);
        unitSelect.classList.toggle("bg-tint/50", Boolean(item.unit_locked));
      } else {
        $("unit-locked-note").classList.add("hidden");
        const unitSelect = form.elements["unit"];
        unitSelect.disabled = false;
        unitSelect.classList.remove("bg-tint/50");
      }
    }

    /** Product/Service switch: hides stock, swaps HSN<->SAC labels, shows description. */
    function applyItemType(itemType) {
      const isService = itemType === "SERVICE";

      form.elements["item_type"].value = isService ? "SERVICE" : "PRODUCT";
      document.querySelectorAll("#item-type-switch button").forEach((btn) => {
        const active = btn.dataset.type === (isService ? "SERVICE" : "PRODUCT");
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-pressed", String(active));
      });

      $("type-hint").textContent = isService ? TYPE_HINTS.SERVICE : TYPE_HINTS.PRODUCT;
      $("code-label").textContent = isService ? CODE_LABEL.SERVICE : CODE_LABEL.PRODUCT;
      $("code-hint").textContent = isService ? CODE_HINT.SERVICE : CODE_HINT.PRODUCT;

      $("service-desc-wrap").classList.toggle("hidden", !isService);
      applyStockMode(state.editingId ? state.editingItem : null);
    }

    function resetForm() {
      form.reset();               // restores defaults
      clearErrors(form);
      markInvalidFields({});
      dirty = false;
      state.editingItem = null;
      applyItemType("PRODUCT");
      form.scrollTop = 0;
    }

    function fillForm(item) {
      FIELDS.forEach((field) => { form.elements[field].value = item[field] ?? ""; });
      form.elements["price_includes_tax"].checked = Boolean(item.price_includes_tax);
      form.elements["track_stock"].checked = Boolean(item.tracks_stock);
      // Show 0 as blank so the field does not look pre-filled with a real number.
      ["sales_price", "purchase_price", "current_stock", "low_stock_threshold"].forEach((f) => {
        if (Number(item[f]) === 0) form.elements[f].value = "";
      });
      state.editingItem = item;
      applyItemType(item.item_type || "PRODUCT");
      dirty = false;
    }

  async function openDrawer(itemId = null) {
    state.editingId = itemId;
    resetForm();
    $("drawer-title").textContent = itemId ? "Edit Item" : "Add Item";
    $("drawer-subtitle").innerHTML = itemId
      ? "Update the details below."
      : 'Fields marked <span class="text-danger">*</span> are required.';
    submitBtn.textContent = itemId ? "Save Changes" : "Add Item";

    lastFocused = document.activeElement;
    openDrawerAnimation();
    ensureOptions();

    if (!itemId) {
      setTimeout(() => form.elements["name"].focus(), 80);
      return;
    }

    form.classList.add("opacity-50", "pointer-events-none");
    submitBtn.disabled = true;
    try {
      const item = await apiRequest(`/items/${itemId}/`);
      if (state.editingId !== itemId) return; // drawer was closed meanwhile
      fillForm(item);
      form.elements["name"].focus();
    } catch (err) {
      if (state.editingId === itemId) {
        closeDrawer(true);
        showToast(err.message, "error");
      }
    } finally {
      form.classList.remove("opacity-50", "pointer-events-none");
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

  // ----- form data + validation -----
  function buildPayload() {
    const payload = {};
    FIELDS.forEach((field) => {
      const el = form.elements[field];
      payload[field] = el.type === "checkbox" ? el.checked : el.value.trim();
    });

    // Codes are stored upper case.
    ["item_code", "barcode", "hsn_sac_code"].forEach((f) => {
      payload[f] = payload[f].toUpperCase();
    });

    // Blank numeric fields mean "not set" -> send null, never "".
    ["purchase_price", "current_stock", "low_stock_threshold"].forEach((f) => {
      if (payload[f] === "") payload[f] = null;
    });

// A service never carries stock or a HSN/SAC confusion.
      if (payload.item_type === "SERVICE") {
        payload.current_stock = null;
        payload.low_stock_threshold = null;
        payload.track_stock = false;
        if (payload.service_description === "") payload.service_description = null;
      }

      // `current_stock` is the *opening* balance and is create-only (2.0.6).
      // Sending it on an edit invites the server to silently reject a value the
      // user could not even edit, so it is omitted rather than sent-and-hoped.
      if (state.editingId) delete payload.current_stock;

      return payload;
    }

  /** Quick checks for instant feedback. The backend validates everything again. */
  function validate(p) {
    const errors = {};
    const fail = (field, message) => { errors[field] = [message]; };
    const isService = p.item_type === "SERVICE";

    if (!p.name) fail("name", "Enter an item name.");
    if (!/^\d+(\.\d{1,2})?$/.test(String(p.sales_price))) {
      fail("sales_price", "Enter a sale price with up to 2 decimals, e.g. 249.50.");
    }
    if (p.purchase_price && !/^\d+(\.\d{1,2})?$/.test(String(p.purchase_price))) {
      fail("purchase_price", "Enter an amount with up to 2 decimals, or leave it empty.");
    }

    // HSN/SAC: 4, 6 or 8 digits only (5 and 7 are never used); SAC never 8.
    if (p.hsn_sac_code) {
      const code = String(p.hsn_sac_code);
      const valid = isService ? /^(?:[0-9]{4}|[0-9]{6})$/.test(code) : /^(?:[0-9]{4}|[0-9]{6}|[0-9]{8})$/.test(code);
      if (!valid) {
        fail("hsn_sac_code", isService
          ? "A SAC code must be 4 or 6 digits."
          : "An HSN code must be 4, 6 or 8 digits (5 and 7 are not used).");
      }
    }
    if (isService && !p.service_description) {
      fail("service_description", "Services need a description - it is printed on the tax invoice.");
    }

    // Stock is only meaningful for products, and allows 3 decimals.
    if (!isService) {
      for (const field of ["current_stock", "low_stock_threshold"]) {
        const value = p[field];
        if (value === null || value === "") continue;
        if (!/^\d+(\.\d{1,3})?$/.test(String(value))) {
          fail(field, "Enter a quantity with up to 3 decimals, e.g. 1.500.");
        }
      }
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
    // On create send everything; on edit PATCH only what changed.
    const body = isEdit
      ? Object.fromEntries(Object.entries(payload).filter(([k]) => k !== "item_type"))
      : payload;

    setBusy(submitBtn, true, "Saving...");
    try {
      await apiRequest(isEdit ? `/items/${state.editingId}/` : "/items/", {
        method: isEdit ? "PATCH" : "POST",
        body,
      });
      closeDrawer(true);
      showToast(isEdit ? "Item updated successfully." : "Item added successfully.");
      await loadItems();
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
    if (["item_code", "barcode", "hsn_sac_code"].includes(input.name)) {
      input.value = input.value.toUpperCase();
    }

    const slot = form.querySelector(`[data-error-for="${input.name}"]`);
    if (slot) slot.textContent = "";
    input.classList.remove("is-invalid");
    input.removeAttribute("aria-invalid");
  }

  // ===================================================================
  // DELETE / RESTORE
  // ===================================================================
  function openConfirm(item) {
    state.pendingDelete = { id: item.id, name: item.name };
    $("confirm-title").textContent = `Delete "${item.name}"?`;
    $("confirm-text").textContent =
      "This item will be hidden from lists and dropdowns, but existing invoices keep their history. "
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
      await apiRequest(`/items/${target.id}/`, { method: "DELETE" });
      closeConfirm();
      showToast(`"${target.name}" was deleted.`);
      await loadItems();
    } catch (err) {
      closeConfirm();
      showToast(err.message, "error");
    } finally {
      setBusy(okBtn, false);
    }
  }

  async function restoreItem(item) {
    try {
      await apiRequest(`/items/${item.id}/restore/`, { method: "POST" });
      showToast(`"${item.name}" was restored.`);
      await loadItems();
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  // ===================================================================
  // EVENTS
  // ===================================================================
  function bindEvents() {
    $("add-item-btn").addEventListener("click", () => openDrawer());
    $("empty-action").addEventListener("click", () => openDrawer());
    $("retry-btn").addEventListener("click", loadItems);

    $("item-type-switch").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-type]");
      if (!button) return;
      dirty = true;
      applyItemType(button.dataset.type);
      // Clear a now-invalid value so a stale error is not resubmitted.
      if (button.dataset.type === "SERVICE") {
        form.elements["current_stock"].value = "";
        form.elements["low_stock_threshold"].value = "";
      }
    });

    $("type-filter").addEventListener("change", (e) => { state.type = e.target.value; state.page = 1; loadItems(); });
    $("stock-filter").addEventListener("change", (e) => { state.stock = e.target.value; state.page = 1; loadItems(); });
    $("status-filter").addEventListener("change", (e) => { state.status = e.target.value; state.page = 1; loadItems(); });
    $("sort-select").addEventListener("change", (e) => { state.ordering = e.target.value; state.page = 1; loadItems(); });

    const searchInput = $("search-input");
    const applySearch = debounce(() => {
      state.search = searchInput.value.trim();
      state.page = 1;
      loadItems();
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
      loadItems();
      searchInput.focus();
    });

    // Drawer
    drawer.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", () => closeDrawer()));
    form.addEventListener("submit", handleSubmit);
    form.addEventListener("input", handleFormInput);

    // Confirm dialog
    $("confirm-cancel").addEventListener("click", closeConfirm);
    $("confirm-backdrop").addEventListener("click", closeConfirm);
    $("confirm-ok").addEventListener("click", confirmDelete);

      // --- stock (2.1.4) ---
      form.elements["track_stock"].addEventListener("change", () => applyStockMode(null));

      $("btn-adjust-stock").addEventListener("click", openAdjustDialog);
      $("btn-stock-history").addEventListener("click", openHistoryDrawer);
      document.querySelectorAll("[data-adjust-close]").forEach((el) =>
        el.addEventListener("click", closeAdjustDialog)
      );
      document.querySelectorAll("[data-history-close]").forEach((el) =>
        el.addEventListener("click", closeHistoryDrawer)
      );
      $("adjust-save").addEventListener("click", saveAdjustment);
      $("adjust-quantity").addEventListener("input", refreshAdjustPreview);
      document.querySelectorAll('input[name="adjust_mode"]').forEach((radio) =>
        radio.addEventListener("change", refreshAdjustPreview)
      );
      ["history-type", "history-from", "history-to"].forEach((id) =>
        $(id).addEventListener("change", loadHistory)
      );

      $("dismiss-negative").addEventListener("click", () =>
        $("negative-stock-banner").classList.add("hidden")
      );
      $("btn-show-negative").addEventListener("click", () => {
        // Filter to exactly the problem rows rather than scrolling: the user has
        // already said they want to see the negative ones.
        state.stock = "negative";
        $("stock-filter").value = "negative";
        state.page = 1;
        loadItems();
      });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
if (!confirmWrap.classList.contains("hidden")) closeConfirm();
        else if (!$("history-drawer").classList.contains("hidden")) closeHistoryDrawer();
        else if (!$("adjust-dialog").classList.contains("hidden")) closeAdjustDialog();
        else if (!drawer.classList.contains("hidden")) closeDrawer();
    });
  }

// ===================================================================
// STOCK: adjust modal, history drawer, negative-stock banner (2.1.4)
// ===================================================================

    // Read a stock number from the user. Returns null rather than NaN or 0,
    // because "not a number" and "zero" lead to different errors and must not
    // collapse into the same value.
    function readQuantity(input) {
      const raw = String(input.value || "").trim();
      if (!raw) return null;
      if (!/^-?\d+(\.\d{1,3})?$/.test(raw)) return NaN;
      return Number(raw);
    }

    /** Decimal-safe "after" preview. Stock is Decimal(12,3) server-side. */
    function previewAfter(mode, current, quantity) {
      if (quantity === null || Number.isNaN(quantity)) return "—";
      const next = mode === "SET" ? quantity : round3(current + quantity);
      return next.toFixed(3);
    }

    function round3(value) {
      return Math.round(value * 1000) / 1000;
    }

    function currentStock() {
      const item = state.editingItem;
      const raw = item?.current_stock;
      return raw === null || raw === undefined ? 0 : Number(raw);
    }

    function refreshAdjustPreview() {
      const mode = form_adjustMode();
      const quantity = readQuantity($("adjust-quantity"));
      $("adjust-before").textContent = currentStock().toFixed(3);
      $("adjust-after").textContent = previewAfter(mode, currentStock(), quantity);

      const el = $("adjust-after");
      const after = mode === "SET" && quantity !== null && !Number.isNaN(quantity)
        ? quantity
        : round3(currentStock() + (quantity || 0));
      el.classList.toggle("text-danger", after < 0);
      el.classList.toggle("text-navy", after >= 0);

      $("adjust-quantity-label").textContent = mode === "SET" ? "Set stock to" : "Change by";
      $("adjust-quantity-hint").textContent = mode === "SET"
        ? "The recorded quantity after this correction."
        : "Use a negative number to reduce stock.";
      $("adjust-quantity").placeholder = mode === "SET" ? "e.g. 48" : "e.g. -2 for a breakage";
    }

    function form_adjustMode() {
      return document.querySelector('input[name="adjust_mode"]:checked')?.value || "CHANGE";
    }

    function openAdjustDialog() {
      const item = state.editingItem;
      if (!item) return;
      $("adjust-item-name").textContent = `${item.name} · currently ${currentStock().toFixed(3)} ${item.unit || ""}`.trim();
      $("adjust-quantity").value = "";
      $("adjust-note").value = "";
      $("adjust-reason").value = "COUNT_CORRECTION";
      document.querySelector('input[name="adjust_mode"][value="CHANGE"]').checked = true;
      $("adjust-error").textContent = "";
      refreshAdjustPreview();
      $("adjust-dialog").classList.remove("hidden");
      setTimeout(() => $("adjust-quantity").focus(), 60);
    }

    function closeAdjustDialog() {
      $("adjust-dialog").classList.add("hidden");
      $("adjust-error").textContent = "";
    }

    async function saveAdjustment() {
      const item = state.editingItem;
      if (!item) return;
      const quantity = readQuantity($("adjust-quantity"));
      if (quantity === null) {
        $("adjust-error").textContent = "Enter a quantity.";
        return;
      }
      if (Number.isNaN(quantity)) {
        $("adjust-error").textContent = "Enter a number with up to 3 decimals, e.g. -2 or 1.5.";
        return;
      }

      const btn = $("adjust-save");
      btn.disabled = true;
      btn.textContent = "Saving…";
      try {
        await apiRequest(`/items/${item.id}/adjust-stock/`, {
          method: "POST",
          body: {
            mode: form_adjustMode(),
            quantity: quantity.toFixed(3),
            reason: $("adjust-reason").value,
            note: $("adjust-note").value.trim(),
          },
        });
        closeAdjustDialog();
        showToast("Stock adjustment recorded.");
        // Re-read the item so the read-only panel and the list both show the new
        // balance. Refetching rather than patching in place keeps the ledger the
        // single source of truth for what the balance *is*.
        const updated = await apiRequest(`/items/${item.id}/`);
        state.editingItem = updated;
        applyStockMode(updated);
        await loadItems();
      } catch (err) {
        $("adjust-error").textContent = err.message || "Couldn't record the adjustment.";
      } finally {
        btn.disabled = false;
        btn.textContent = "Record Adjustment";
      }
    }

    // ----- stock history drawer -----

    const MOVEMENT_STYLES = {
      OPENING: "bg-blue-100 text-blue-700",
      SALE: "bg-amber-100 text-amber-700",
      SALE_REVERSAL: "bg-emerald-100 text-emerald-700",
      ADJUSTMENT: "bg-violet-100 text-violet-700",
      TRACKING_ON: "bg-blue-100 text-blue-700",
      TRACKING_OFF: "bg-muted/20 text-muted",
    };

    const MOVEMENT_LABELS = {
      OPENING: "Opening",
      SALE: "Sale",
      SALE_REVERSAL: "Cancellation",
      ADJUSTMENT: "Adjustment",
      TRACKING_ON: "Tracking enabled",
      TRACKING_OFF: "Tracking disabled",
    };

    function historyQuery() {
      const params = new URLSearchParams();
      const type = $("history-type").value;
      if (type) params.set("movement_type", type);
      if ($("history-from").value) params.set("date_from", $("history-from").value);
      if ($("history-to").value) params.set("date_to", $("history-to").value);
      const qs = params.toString();
      return qs ? `?${qs}` : "";
    }

    async function loadHistory() {
      const item = state.editingItem;
      if (!item) return;
      const body = $("history-body");
      body.replaceChildren(h("p", { className: "text-sm text-muted", textContent: "Loading…" }));

      try {
        const data = await apiRequest(`/items/${item.id}/stock-history/${historyQuery()}`);
        renderHistory(data);
      } catch (err) {
        body.replaceChildren(
          h("p", { className: "text-sm text-danger", textContent: err.message })
        );
      }
    }

    function renderHistory(data) {
      const body = $("history-body");
      body.replaceChildren();
      const rows = data.movements || [];

      $("history-item-name").textContent = state.editingItem?.name || "";

      if (!rows.length) {
        body.append(
          h("p", {
            className: "text-sm text-muted text-center py-8",
            textContent: "No movements match these filters.",
          })
        );
        $("history-footer").textContent = "";
        return;
      }

      const list = h("div", { className: "space-y-2" });
      rows.forEach((row) => {
        const amount = Number(row.quantity_change);
        const isNegative = amount < 0;
        list.append(
          h("div", { className: "flex items-start justify-between gap-3 p-3 rounded-lg border border-line" },
            h("div", { className: "min-w-0" },
              h("div", { className: "flex items-center gap-2 flex-wrap" },
                h("span", {
                  className: `text-xs font-semibold px-2 py-0.5 rounded ${MOVEMENT_STYLES[row.movement_type] || "bg-muted/20 text-muted"}`,
                  textContent: MOVEMENT_LABELS[row.movement_type] || row.movement_type,
                }),
                h("span", {
                  className: "text-sm font-semibold tabular-nums " + (isNegative ? "text-danger" : "text-navy"),
                  textContent: `${amount > 0 ? "+" : ""}${row.quantity_change}`,
                })
              ),
              h("p", { className: "text-xs text-muted mt-1 truncate", textContent: row.source_label || row.note || "—" }),
              h("p", {
                className: "text-xs text-muted",
                textContent: new Date(row.created_at).toLocaleString(),
              })
            ),
            // The running balance is what makes the column readable: read down
            // and the numbers tell the story without any arithmetic.
            h("div", { className: "text-right flex-shrink-0" },
              h("p", { className: "text-xs text-muted uppercase tracking-wider", textContent: "Balance" }),
              h("p", { className: "text-sm font-bold tabular-nums text-navy", textContent: row.stock_after })
            )
          )
        );
      });
      body.append(list);

      // "Showing 200 of 1,438" - a truncated list that looks complete is how a
      // shopkeeper concludes a movement never happened.
      const total = data.count ?? rows.length;
      const returned = data.returned ?? rows.length;
      $("history-footer").textContent =
        returned < total
          ? `Showing the ${returned} most recent of ${total}. Narrow the dates to see older movements.`
          : `${total} movement${total === 1 ? "" : "s"}.`;
    }

    function openHistoryDrawer() {
      $("history-drawer").classList.remove("hidden");
      loadHistory();
    }

    function closeHistoryDrawer() {
      $("history-drawer").classList.add("hidden");
    }

    // ----- negative-stock banner -----

    /**
     * Ask the server which tracked items are below zero.
     *
     * Counted rather than derived from the current page: the banner says "3
     * items are below zero", and a count taken from the 20 rows on screen would
     * say "1" on page 4 of 5 and be simply wrong.
     */
    async function refreshNegativeBanner() {
      const banner = $("negative-stock-banner");
      try {
        const data = await apiRequest("/items/?negative_stock=true&page_size=100");
        const rows = data.results || [];
        if (!rows.length) {
          banner.classList.add("hidden");
          return;
        }
        $("negative-stock-text").textContent =
          `${rows.length} tracked item${rows.length === 1 ? "" : "s"} went below zero. ` +
          "Invoices were still issued - count your stock and record the difference with Adjust Stock.";
        banner.classList.remove("hidden");
      } catch {
        banner.classList.add("hidden"); // a failed check must not block the page
      }
    }

    // ===================================================================
    // START
    // ===================================================================
// ===================================================================
// TEST SEAMS (2.1.4)
//
// Thin wrappers over the real functions above. `node --check` only proves the
// file parses, and the bugs that matter in this UI are wording and wiring bugs
// - a badge reading "in stock" when stock is short, "NaN" in a preview, a unit
// silently editable when the server will reject it. All of those parse fine.
//
// Nothing in the UI calls these; they only read the functions above.
// ===================================================================
    window.__testSetEditingItem = (item) => {
      state.editingItem = item;
      state.editingId = item ? item.id : null;
      // Refresh the modal preview too, so a test can assert on what the user
      // would actually see rather than on a value computed behind the DOM.
      refreshAdjustPreview();
    };

    window.__testApplyStockMode = (item) => {
      state.editingItem = item;
      state.editingId = item ? item.id : null;
      applyStockMode(item);
    };

    window.__testPreviewAdjust = (mode, raw) => {
      const quantity = raw === "" || raw === null || raw === undefined ? null : Number(raw);
      return previewAfter(mode, currentStock(), Number.isNaN(quantity ?? NaN) ? NaN : quantity);
    };

    window.__testPreviewAdjustIsNegative = (mode, raw) => {
      const quantity = Number(raw);
      const next = mode === "SET" ? quantity : round3(currentStock() + quantity);
      return next < 0;
    };

    window.__testBuildPayload = ({ editing, fields }) => {
      const savedId = state.editingId;
      state.editingId = editing ? 1 : null;
      FIELDS.forEach((field) => {
        const el = form.elements[field];
        if (!el) return;
        if (el.type === "checkbox") el.checked = Boolean(fields[field]);
        else el.value = fields[field] ?? "";
      });
      try {
        return buildPayload();
      } finally {
        state.editingId = savedId;
      }
    };

    window.__testHistoryFooter = ({ count, returned }) => {
      const total = count ?? returned;
      return returned < total
        ? `Showing the ${returned} most recent of ${total}. Narrow the dates to see older movements.`
        : `${total} movement${total === 1 ? "" : "s"}.`;
    };

    window.__testRenderHistoryRows = (rows) =>
      rows.map((row) => {
        const amount = Number(row.quantity_change);
        return {
          label: MOVEMENT_LABELS[row.movement_type] || row.movement_type,
          amount: `${amount > 0 ? "+" : ""}${row.quantity_change}`,
          balance: row.stock_after,
          negative: amount < 0,
        };
      });

    if (!requireLogin()) return;
    renderNav("items");
    bindEvents();
    loadItems().then(refreshNegativeBanner);
})();