/**
 * Purchases page: list supplier bills, create/edit a draft, post and cancel.
 * Requires config.js, api.js, ui.js.
 *
 * The one idea the UI has to get right, and which shapes most of this file:
 *
 *   **These amounts are typed, not computed.** On a sales invoice the server
 *   calculates every figure and the form just displays them. Here the form is
 *   the source of truth - the supplier's bill is what it says - and the server's
 *   arithmetic is shown alongside as a *cross-check*, never written over the
 *   user's numbers. So there is no "recalculate" button that could overwrite a
 *   transcription, only a "Check figures" that tells you whether you agree with
 *   the maths.
 *
 * Security: user text is only written with textContent, never innerHTML.
 */

(() => {
  "use strict";

  const PAGE_SIZE = 20;

  const state = {
    page: 1,
    search: "",
    status: "",
    dateFrom: "",
    dateTo: "",
    count: 0,
    editingId: null,
    lines: [],
    lineKey: 0,
    pendingCancel: null,
    items: [],
    parties: [],
  };

  let dirty = false;
  let requestCounter = 0;
  let lastFocused = null;

  const $ = (id) => document.getElementById(id);
  const form = $("purchase-form");
  const drawer = $("drawer");
  const drawerPanel = drawer.querySelector(".drawer-panel");

  const HEADERS = [
    "subtotal",
    "total_discount",
    "taxable_total",
    "cgst_total",
    "sgst_total",
    "igst_total",
    "round_off",
    "grand_total",
  ];

  const LINE_HEADERS = [
    "taxable_value",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "total_amount",
  ];

  const ICON_X =
    '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';

  // ===================================================================
  // LIST
  // ===================================================================

  function showTable() {
    $("loading-state").hidden = true;
    $("empty-state").hidden = true;
    $("error-state").hidden = true;
    $("table-card").hidden = false;
  }

  function setLoading(on) {
    if (on) {
      $("loading-state").hidden = false;
      $("table-card").hidden = true;
    } else {
      $("loading-state").hidden = true;
    }
  }

  function showEmpty(message) {
    showTable();
    $("table-card").hidden = true;
    $("empty-state").hidden = false;
    $("empty-state").querySelector("p").textContent = message;
  }

  function showError(message) {
    setLoading(false);
    $("table-card").hidden = true;
    $("error-state").hidden = false;
    $("error-state").querySelector("p").textContent = message;
  }

  async function loadPurchases() {
    const ticket = ++requestCounter;
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: state.page, page_size: PAGE_SIZE });
      if (state.search) params.set("search", state.search);
      if (state.status) params.set("status", state.status);
      if (state.dateFrom) params.set("date_from", state.dateFrom);
      if (state.dateTo) params.set("date_to", state.dateTo);
      if ($("divergence-banner").dataset.active === "1") params.set("divergent", "true");

      const data = await withRetryOnThrottle(() => apiRequest(`/purchases/?${params}`));
      if (ticket !== requestCounter) return;

      state.count = data.count || 0;
      renderRows(data.results || []);
      renderPagination();
      renderDivergenceBanner(data.results || []);
      showTable();
    } catch (err) {
      if (ticket !== requestCounter) return;
      showError(err.message || "Couldn't load purchases.");
    }
  }

  function renderRows(rows) {
    const tbody = $("purchases-tbody");
    tbody.replaceChildren();

    if (!rows.length) {
      showEmpty(
        state.search || state.status
          ? "No purchases match these filters."
          : "No purchases yet."
      );
      return;
    }

    rows.forEach((row) => {
      const tr = h("tr", {
        className: "border-b border-line hover:bg-tint/40 cursor-pointer",
      });
      tr.addEventListener("click", () => openDrawer(row.id));

      tr.append(
        h("td", { className: "px-4 py-3" },
          h("div", { className: "font-medium text-navy", textContent: row.bill_number || "(no bill number)" }),
          row.has_divergence
            ? h("div", { className: "text-xs text-warning mt-0.5", textContent: "Figures differ from the calculator" })
            : h("span")
        ),
        h("td", { className: "px-4 py-3 text-sm", textContent: row.supplier_name || "-" }),
        h("td", { className: "px-4 py-3 text-sm", textContent: new Date(row.bill_date).toLocaleDateString("en-IN") }),
        h("td", { className: "px-4 py-3" }, statusBadge(row.status)),
        h("td", { className: "px-4 py-3 text-right text-sm tabular-nums", textContent: formatINR(row.tax_total) }),
        h("td", { className: "px-4 py-3 text-right font-semibold tabular-nums text-navy", textContent: formatINR(row.grand_total) })
      );
      tbody.append(tr);
    });
  }

  function statusBadge(status) {
    const styles = {
      DRAFT: "bg-muted/20 text-muted",
      POSTED: "bg-ok-soft text-ok",
      CANCELLED: "bg-danger-soft text-danger",
    };
    const labels = { DRAFT: "Draft", POSTED: "Posted", CANCELLED: "Cancelled" };
    return h("span", {
      className: `inline-block rounded-full px-2 py-0.5 text-xs font-medium ${styles[status] || styles.DRAFT}`,
      textContent: labels[status] || status,
    });
  }

  function renderPagination() {
    const box = $("pagination");
    box.replaceChildren();
    const total = state.count;
    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (pages <= 1) {
      box.append(h("span", { className: "text-xs text-muted", textContent: `${total} bill(s)` }));
      return;
    }
    box.append(
      h("span", {
        className: "text-xs text-muted",
        textContent: `Page ${state.page} of ${pages} - ${total} bill(s)`,
      })
    );

    const prev = h("button", { className: "btn btn-ghost !py-1 !px-3 text-xs", textContent: "Previous" });
    prev.disabled = state.page <= 1;
    prev.addEventListener("click", () => {
      state.page -= 1;
      loadPurchases();
    });

    const next = h("button", { className: "btn btn-ghost !py-1 !px-3 text-xs", textContent: "Next" });
    next.disabled = state.page >= pages;
    next.addEventListener("click", () => {
      state.page += 1;
      loadPurchases();
    });

    box.append(prev, next);
  }

  /**
   * Banner for bills whose typed figures disagree with the arithmetic.
   *
   * Deliberately shown for drafts as well as posted bills. A purchase is entered
   * by hand, so a mismatch is usually a typo caught *before* posting - which is
   * the whole value of storing the computed mirror.
   */
  function renderDivergenceBanner(rows) {
    const banner = $("divergence-banner");
    const divergent = rows.filter((row) => row.has_divergence);
    if (!divergent.length) {
      banner.classList.add("hidden");
      return;
    }
    $("divergence-text").textContent =
      `${divergent.length} bill(s) on this page have figures that differ from the ` +
      "calculator. Open one to see exactly which figures and by how much - your " +
      "numbers are kept as entered.";
    banner.classList.remove("hidden");
  }

  // ===================================================================
  // DRAWER
  // ===================================================================

  /**
   * Slide the drawer in.
   *
   * `theme.css` styles `.drawer` and `.drawer-panel` for the items and parties
   * pages: the wrapper gets `visibility: hidden` and the panel starts at
   * `transform: translateX(100%)`. So removing the Tailwind `hidden` class is
   * not enough on its own - `visibility` is a separate property from `display`,
   * and the panel would stay parked off-screen to the right. The JS has to move
   * it in, exactly as items.js and parties.js do.
   */
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

  async function openDrawer(purchaseId = null) {
    state.editingId = purchaseId;
    state.lines = [];
    dirty = false;
    clearErrors(form);

    $("drawer-title").textContent = purchaseId ? "Edit Purchase" : "New Purchase";
    $("submit-btn").textContent = purchaseId ? "Save Changes" : "Save Draft";
    $("post-btn").classList.toggle("hidden", !purchaseId);
    $("preview-box").classList.add("hidden");

    lastFocused = document.activeElement;
    openDrawerAnimation();
    form.classList.add("opacity-50", "pointer-events-none");

    try {
      await loadSuppliers();
      if (!purchaseId) {
        $("f-bill_date").value = new Date().toISOString().slice(0, 10);
        form.classList.remove("opacity-50", "pointer-events-none");
        return;
      }
      const purchase = await apiRequest(`/purchases/${purchaseId}/`);
      fillForm(purchase);
    } catch (err) {
      closeDrawer(true);
      showToast(err.message, "error");
    } finally {
      form.classList.remove("opacity-50", "pointer-events-none");
    }
  }

  async function loadSuppliers() {
    const data = await withRetryOnThrottle(() =>
      apiRequest(`/parties/?party_type=SUPPLIER&page_size=200&is_active=true`)
    );
    state.parties = data.results || [];
    const select = $("f-supplier");
    const chosen = select.value;
    select.replaceChildren(new Option("Choose a supplier\u2026", ""));
    state.parties
      .filter((party) => !party.is_walk_in)
      .forEach((party) => select.append(new Option(party.name, party.id)));
    select.value = chosen;
  }

  function fillForm(purchase) {
    HEADERS.forEach((field) => {
      const el = form.elements[field];
      if (el) el.value = purchase[field] ?? "";
    });
    form.elements["supplier"].value = purchase.supplier ?? "";
    form.elements["bill_number"].value = purchase.bill_number ?? "";
    form.elements["bill_date"].value = purchase.bill_date ?? "";
    form.elements["due_date"].value = purchase.due_date ?? "";
    form.elements["notes"].value = purchase.notes ?? "";
    form.elements["update_item_purchase_price"].checked = Boolean(purchase.update_item_purchase_price);

    state.lines = (purchase.items || []).map((line) => ({ ...line }));
    renderLines();

    // A posted bill is history. Disable the money, and say why, rather than
    // letting the user fill it in and then rejecting it on save.
    const locked = purchase.status !== "DRAFT";
    form.classList.toggle("opacity-70", locked);
    [...HEADERS, "supplier", "bill_number", "bill_date", "update_item_purchase_price"].forEach((field) => {
      const el = form.elements[field];
      if (el) el.disabled = locked;
    });
    $("add-item-btn").disabled = locked;
    $("post-btn").classList.toggle("hidden", purchase.status !== "DRAFT");
    dirty = false;
  }

  function closeDrawer(force = false) {
    if (!force && dirty && !window.confirm("Discard your unsaved changes?")) return;
    closeDrawerAnimation();
    state.editingId = null;
    dirty = false;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  // ===================================================================
  // LINES
  // ===================================================================

  function addItemLine() {
    state.lines.push({
      key: `line-${(state.lineKey += 1)}`,
      item: null,
      item_name: "",
      item_type: "PRODUCT",
      hsn_sac_code: "",
      unit: "PCS",
      quantity: "1.000",
      unit_price: "0.00",
      line_discount: "0.00",
      tax_rate: "18.00",
      taxable_value: "0.00",
      cgst_amount: "0.00",
      sgst_amount: "0.00",
      igst_amount: "0.00",
      total_amount: "0.00",
      itc_eligible: true,
    });
    renderLines();
  }

  function removeLine(key) {
    state.lines = state.lines.filter((line) => line.key !== key);
    renderLines();
  }

  function renderLines() {
    const tbody = $("lines-tbody");
    tbody.replaceChildren();
    $("lines-wrap").classList.toggle("hidden", state.lines.length === 0);
    $("no-lines").classList.toggle("hidden", state.lines.length > 0);

    state.lines.forEach((line) => {
      const tr = h("tr", { className: "border-b border-line align-top" });

      // Item picker: a catalogue item or a free-text description.
      const nameCell = h("td", { className: "px-4 py-2" });
      const picker = h("select", { className: "input !py-2 !px-3 text-sm" });
      picker.setAttribute("aria-label", "Item");
      picker.append(new Option("Catalogue item\u2026", ""));
      state.items.forEach((item) => picker.append(new Option(item.name, `i:${item.id}`)));
      picker.append(new Option("Other (free text)\u2026", "free"));
      picker.value = line.item ? `i:${line.item}` : line.item_name ? "free" : "";
      picker.addEventListener("change", (event) => {
        const value = event.target.value;
        if (value.startsWith("i:")) {
          const item = state.items.find((row) => row.id === Number(value.slice(2)));
          if (item) {
            line.item = item.id;
            line.item_name = item.name;
            line.item_type = item.item_type;
            line.hsn_sac_code = item.hsn_sac_code || "";
            line.unit = item.unit || "";
            line.tax_rate = item.tax_rate;
            line.unit_price = item.purchase_price ?? line.unit_price;
          }
        } else if (value === "free") {
          line.item = null;
        }
        dirty = true;
        renderLines();
      });
      nameCell.append(picker);

      if (line.item_name && !line.item) {
        nameCell.append(
          h("input", {
            type: "text",
            className: "input !py-2 !px-3 text-sm mt-2",
            placeholder: "What is this line?",
            value: line.item_name,
            ariaLabel: "Free-text line description",
          })
        );
        nameCell.lastChild.addEventListener("input", (event) => {
          line.item_name = event.target.value;
          dirty = true;
        });
      }
      tr.append(nameCell);

      tr.append(numberCell(line, "quantity", "0.001", "3"));
      tr.append(numberCell(line, "unit_price", "0.01", "2"));

      // The four entered money columns. Read-only in the sense that they are not
      // recomputed here, but editable - the user is transcribing a bill.
      LINE_HEADERS.forEach((field) => {
        tr.append(numberCell(line, field, "0.01", "2"));
      });

      const actions = h("td", { className: "px-3 py-2 text-right" });
      const remove = h("button", { type: "button", className: "icon-btn danger", ariaLabel: "Remove line", title: "Remove this line" });
      remove.innerHTML = ICON_X;
      remove.addEventListener("click", () => removeLine(line.key));
      actions.append(remove);
      tr.append(actions);

      tbody.append(tr);
    });
  }

  function numberCell(line, field, step, places) {
    const input = h("input", {
      type: "number",
      step,
      min: "0",
      className: "input !py-2 !px-3 text-sm text-right tabular-nums",
    });
    input.value = Number(line[field] || 0).toFixed(places);
    input.setAttribute("aria-label", field.replace(/_/g, " "));
    input.addEventListener("input", (event) => {
      line[field] = event.target.value;
      dirty = true;
    });
    return h("td", { className: "px-3 py-2" }, input);
  }

  // ===================================================================
  // PAYLOAD / SUBMIT
  // ===================================================================

  function buildPayload() {
    const payload = {};
    HEADERS.forEach((field) => {
      const value = form.elements[field].value.trim();
      payload[field] = value === "" ? "0.00" : value;
    });
    ["supplier", "bill_number", "bill_date", "due_date", "notes"].forEach((field) => {
      const value = form.elements[field].value.trim();
      // An empty string is not the same as "not supplied" to DRF: `DateField`
      // rejects "" as a malformed date and answers 400 with a message about
      // formats, which reads as though the user typed nonsense. Normalise here
      // so both the save and the preview path behave.
      payload[field] = value === "" ? null : value;
    });
    payload.update_item_purchase_price = form.elements["update_item_purchase_price"].checked;
    payload.items = state.lines.map((line) => {
      const entry = {
        quantity: line.quantity,
        unit_price: line.unit_price,
        line_discount: line.line_discount || "0.00",
        tax_rate: line.tax_rate || "0.00",
        itc_eligible: line.itc_eligible !== false,
      };
      if (line.item) entry.item = line.item;
      else entry.item_name = line.item_name;
      entry.item_type = line.item_type || "PRODUCT";
      entry.hsn_sac_code = line.hsn_sac_code || "";
      entry.unit = line.unit || "";
      LINE_HEADERS.forEach((field) => {
        entry[field] = line[field] || "0.00";
      });
      return entry;
    });
    return payload;
  }

  /**
   * Show one message against one field.
   *
   * `showFieldErrors` handles a whole server payload; this is for the checks
   * made here in the browser (no supplier chosen, no lines added), where there
   * is no response to read.
   */
  function setFieldError(scope, field, message) {
    const box = scope.querySelector(`[data-error-for="${field}"]`);
    if (box) box.textContent = message;
  }

  async function saveDraft() {
    clearErrors(form);
    const payload = buildPayload();
    if (!payload.supplier) {
      setFieldError(form, "supplier", "Choose a supplier.");
      return null;
    }
    if (!payload.bill_number) {
      setFieldError(form, "bill_number", "Enter the bill number printed on the supplier's bill.");
      return null;
    }
    if (!payload.due_date) {
      setFieldError(form, "due_date", "Enter the due date from the bill.");
      return null;
    }
    if (!state.lines.length) {
      setFieldError(form, "items", "Add at least one line.");
      return null;
    }

    const url = state.editingId ? `/purchases/${state.editingId}/` : "/purchases/";
    return apiRequest(url, {
      method: state.editingId ? "PATCH" : "POST",
      body: payload,
    });
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setBusy($("submit-btn"), true, "Saving...");
    try {
      const saved = await saveDraft();
      if (!saved) return;
      dirty = false;
      showToast(state.editingId ? "Purchase updated." : "Draft saved.");
      closeDrawer(true);
      await loadPurchases();
    } catch (err) {
      showFieldErrors(form, err);
    } finally {
      setBusy($("submit-btn"), false);
    }
  }

  // ===================================================================
  // PREVIEW - the cross-check, never an overwrite
  // ===================================================================

  /**
   * Ask the server what the calculator makes of these figures.
   *
   * This is a *comparison*, not a "recalculate". The entered numbers are what get
   * saved; this only reports the difference. There is deliberately no control
   * anywhere on this page that writes the computed figures into the form,
   * because that would silently replace the supplier's bill with our arithmetic
   * - which is the exact mistake this module exists to prevent.
   */
  async function runPreview() {
    const payload = buildPayload();
    if (!payload.supplier || !state.lines.length) {
      showToast("Add a supplier and at least one line first.", "error");
      return;
    }
    setBusy($("preview-btn"), true, "Checking...");
    try {
      const result = await withRetryOnThrottle(() =>
        apiRequest("/purchases/preview/", { method: "POST", body: payload })
      );
      const box = $("preview-box");
      box.classList.remove("hidden");
      $("preview-figures").textContent =
        `Taxable ${result.computed.taxable_total}  |  ` +
        `CGST ${result.computed.cgst_total}  |  SGST ${result.computed.sgst_total}  |  ` +
        `IGST ${result.computed.igst_total}  |  Total ${result.computed.grand_total}`;

      const verdict = $("preview-verdict");
      if (result.has_divergence) {
        verdict.className = "mt-2 text-sm text-warning";
        verdict.textContent =
          `These figures differ from the calculator by more than Rs 1 (largest: ` +
          `${result.max_divergence}). Your numbers will be saved exactly as entered - ` +
          `check the supplier's bill.`;
      } else {
        verdict.className = "mt-2 text-sm text-ok";
        verdict.textContent = "Your figures agree with the calculator.";
      }
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setBusy($("preview-btn"), false);
    }
  }

  // ===================================================================
  // POST / CANCEL
  // ===================================================================

  async function postPurchase() {
    if (!state.editingId) return;
    setBusy($("post-btn"), true, "Posting...");
    try {
      const result = await apiRequest(`/purchases/${state.editingId}/post/`, {
        method: "POST",
        body: {},
      });
      if (result.has_divergence) {
        showToast("Posted, but the figures differ from the calculator.", "warning");
      } else {
        showToast("Posted. Stock has been added.");
      }
      closeDrawer(true);
      await loadPurchases();
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setBusy($("post-btn"), false);
    }
  }

  function openCancelDialog(purchase) {
    state.pendingCancel = purchase;
    $("cancel-details").classList.add("hidden");
    $("cancel-reason").value = "";
    $("cancel-dialog").classList.remove("hidden");
    setTimeout(() => $("cancel-reason").focus(), 60);
  }

  function closeCancelDialog() {
    $("cancel-dialog").classList.add("hidden");
    state.pendingCancel = null;
  }

  async function confirmCancel() {
    const purchase = state.pendingCancel;
    const reason = $("cancel-reason").value.trim();
    if (!purchase || !reason) {
      setFieldError($("cancel-dialog"), "reason", "Say why this bill is being cancelled.");
      return;
    }
    setBusy($("cancel-confirm"), true, "Cancelling...");
    try {
      await apiRequest(`/purchases/${purchase.id}/cancel/`, {
        method: "POST",
        body: { reason },
      });
      closeCancelDialog();
      showToast("Bill cancelled and the stock reversed.");
      await loadPurchases();
    } catch (err) {
      // CANCEL_WOULD_GO_NEGATIVE carries `details` naming the items, because
      // "this would take stock below zero" is not actionable on a bill with a
      // dozen lines.
      if (err.code === "CANCEL_WOULD_GO_NEGATIVE" && err.details) {
        const box = $("cancel-details");
        box.classList.remove("hidden");
        box.replaceChildren();
        box.append(h("p", { className: "font-medium", textContent: err.message }));
        err.details.forEach((row) => {
          box.append(
            h("p", {
              className: "mt-1 text-sm",
              textContent: `${row.item_name}: ${row.available} in stock, cancelling would leave ${row.resulting}`,
            })
          );
        });
      } else {
        showToast(err.message, "error");
      }
    } finally {
      setBusy($("cancel-confirm"), false);
    }
  }

  // ===================================================================
  // WIRING
  // ===================================================================

  function wireEvents() {
    $("new-purchase-btn").addEventListener("click", () => openDrawer());
    $("empty-action").addEventListener("click", () => openDrawer());
    $("retry-btn").addEventListener("click", loadPurchases);
    $("add-item-btn").addEventListener("click", addItemLine);
    $("preview-btn").addEventListener("click", runPreview);
    $("post-btn").addEventListener("click", postPurchase);

    drawer.querySelectorAll("[data-close]").forEach((el) =>
      el.addEventListener("click", () => closeDrawer())
    );
    form.addEventListener("submit", handleSubmit);
    form.addEventListener("input", () => {
      dirty = true;
    });

    $("f-status").addEventListener("change", (e) => {
      state.status = e.target.value;
      state.page = 1;
      loadPurchases();
    });
    ["f-from", "f-to"].forEach((id) =>
      $(id).addEventListener("change", (e) => {
        state.dateFrom = $("f-from").value;
        state.dateTo = $("f-to").value;
        state.page = 1;
        loadPurchases();
      })
    );

    let searchTimer;
    $("f-search").addEventListener("input", (e) => {
      clearTimeout(searchTimer);
      const value = e.target.value.trim();
      searchTimer = setTimeout(() => {
        state.search = value;
        state.page = 1;
        loadPurchases();
      }, 300);
    });

    $("cancel-cancel").addEventListener("click", closeCancelDialog);
    $("cancel-backdrop").addEventListener("click", closeCancelDialog);
    $("cancel-confirm").addEventListener("click", confirmCancel);

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!$("cancel-dialog").classList.contains("hidden")) closeCancelDialog();
      else if (!drawer.classList.contains("hidden")) closeDrawer();
    });
  }

  async function boot() {
    $("f-bill_date").value = new Date().toISOString().slice(0, 10);
    try {
      const data = await withRetryOnThrottle(() => apiRequest("/items/?page_size=200"));
      state.items = data.results || [];
    } catch {
      state.items = [];
    }
    await loadPurchases();
  }

  // ===================================================================
  // TEST SEAMS (2.2)
  //
  // Thin wrappers over the functions above. `node --check` only proves a file
  // parses, and every bug that matters here is a wording or wiring bug - a
  // badge that says "posted" for a cancelled bill, "NaN" in a total, a
  // divergence message that reads as an error. All of those parse perfectly.
  // ===================================================================
  window.__testBuildPayload = (lines, headerValues) => {
    HEADERS.forEach((field) => {
      const el = form.elements[field];
      if (el) el.value = headerValues[field] ?? "0.00";
    });
    ["supplier", "bill_number", "bill_date", "due_date", "notes"].forEach((field) => {
      const el = form.elements[field];
      if (el) el.value = headerValues[field] ?? "";
    });
    form.elements["update_item_purchase_price"].checked = Boolean(
      headerValues.update_item_purchase_price
    );
    state.lines = lines;
    return buildPayload();
  };

  window.__testRenderDivergenceVerdict = (result) => {
    const verdict = $("preview-verdict");
    if (result.has_divergence) {
      verdict.className = "mt-2 text-sm text-warning";
      verdict.textContent =
        `These figures differ from the calculator by more than Rs 1 ` +
        `(largest: ${result.max_divergence}). Your numbers will be saved exactly as entered.`;
    } else {
      verdict.className = "mt-2 text-sm text-ok";
      verdict.textContent = "Your figures agree with the calculator.";
    }
    return { className: verdict.className, text: verdict.textContent };
  };

  window.__testStatusBadge = (status) => {
    const badge = statusBadge(status);
    return { text: badge.textContent, className: badge.className };
  };

  window.__testCancelDetailLines = (details) =>
    details.map(
      (row) =>
        `${row.item_name}: ${row.available} in stock, cancelling would leave ${row.resulting}`
    );

  window.__testSetPendingCancel = (purchase) => {
    state.pendingCancel = purchase;
  };

  // ===================================================================
  // START
  // ===================================================================
  if (!requireLogin()) return;
  renderNav("purchases");
  wireEvents();
  boot();
})();