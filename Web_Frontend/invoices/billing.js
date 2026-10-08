/**
 * Invoice billing screen.
 *
 * THE ONE RULE: this file never computes GST.
 *
 * Every taxable value, CGST/SGST/IGST amount, discount share and grand total
 * shown here is whatever `POST /api/v1/invoices/preview/` returned. The browser
 * does not add tax, does not apportion discounts, and does not decide
 * CGST-vs-IGST. It only *displays* the server's answer, and the same server code
 * runs again when the invoice is saved - so what the user sees before saving is
 * exactly what gets stored.
 *
 * Consequence: any drift between the preview and the saved invoice is a backend
 * bug, and `tests/test_preview_drift.py` fails if one appears. Adding a
 * `lineTotal = qty * price * (1 + rate/100)` here would reintroduce the exact
 * class of bug this design exists to prevent.
 *
 * Requires config.js, api.js, ui.js.
 */
(() => {
  "use strict";

  const PREVIEW_DEBOUNCE_MS = 300;

  const ITEM_TYPE_LABELS = { PRODUCT: "Goods", SERVICE: "Service" };

  // Static icons only (no user data), so innerHTML is safe here.
  const ICON_X = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';
  const ICON_SUGGEST = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>';

  const state = {
    invoiceId: null,
    status: "DRAFT",
    invoice: null,          // last saved/loaded invoice from the API
    party: null,            // { id, name, state_code, shipping_state_code, gstin, ... }
    placeOfSupply: "",
    priceIncludesTax: false,
    lines: [],              // [{ key, mode: "item"|"custom", item, ... , preview }]
  };
  let previewTotals = null;
  let meta = { units: [], rates: [], states: [], profile: null };
  let previewTicket = 0;    // ignores out-of-date preview responses
  let lineKey = 0;

  const $ = (id) => document.getElementById(id);
  const form = $("invoice-form");

  const toMoney = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
  const rupees = (value) => `₹${formatINR(toMoney(value))}`;

  // ===================================================================
  // BOOT
  // ===================================================================
  async function boot() {
    if (!requireLogin()) return;
    renderNav("invoices");

    const id = new URLSearchParams(window.location.search).get("id");
    $("invoice-date").value = new Date().toISOString().slice(0, 10);

    try {
      // Everything the form needs, in one round trip.
      const [states, units, rates, profile] = await Promise.all([
        apiRequest("/meta/states/"),
        apiRequest("/meta/units/"),
        apiRequest("/meta/tax-rates/"),
        apiRequest("/business/profile/"),
      ]);
      meta = { states, units, rates, profile };

      states.forEach((s) => $("place-of-supply").append(new Option(`${s.code} - ${s.name}`, s.code)));
      units.forEach((u) => $("custom-unit").append(new Option(`${u.name} (${u.code})`, u.code)));
      rates.forEach((r) => $("custom-rate").append(new Option(r.name, r.code)));

      $("round-total").checked = Boolean(profile.round_invoice_total);
      state.priceIncludesTax = false;

      if (id) await loadInvoice(Number(id));
      else setDefaultPlaceOfSupply();

      renderLines();
      schedulePreview();
    } catch (err) {
      showFormMessage(form, err.message);
    }
  }

  function setDefaultPlaceOfSupply() {
    // Walk-in / unset party: the business's own state (decision 9).
    const businessState = meta.profile?.state_code || "";
    if (businessState) {
      $("place-of-supply").value = businessState;
      state.placeOfSupply = businessState;
    }
  }

  async function loadInvoice(id) {
    const invoice = await apiRequest(`/invoices/${id}/`);
    state.invoiceId = invoice.id;
    state.invoice = invoice;
    state.status = invoice.status;

    $("page-title").textContent = invoice.display_number;
    $("page-subtitle").textContent = invoice.status === "DRAFT"
      ? "Draft - edit freely, it is numbered only when you issue it"
      : `Issued ${new Date(invoice.issued_at || invoice.invoice_date).toLocaleDateString("en-IN")}`;

    const badge = $("status-badge");
    badge.textContent = invoice.status === "DRAFT" ? "Draft"
      : invoice.status === "ISSUED" ? "Issued" : "Cancelled";
    badge.className = `badge self-center ${
      invoice.status === "ISSUED" ? "bg-ok-soft text-ok"
        : invoice.status === "CANCELLED" ? "bg-danger-soft text-danger" : "badge-muted"}`;

    $("invoice-date").value = invoice.invoice_date;
    $("due-date").value = invoice.due_date || "";
    $("notes").value = invoice.notes || "";
    $("terms").value = invoice.terms || "";
    $("invoice-discount").value = invoice.invoice_discount || "0.00";
    $("prices-include-tax").checked = Boolean(invoice.prices_include_tax);
    state.priceIncludesTax = Boolean(invoice.prices_include_tax);

    await selectPartyById(invoice.party, { silent: true });

    if (invoice.place_of_supply) {
      $("place-of-supply").value = invoice.place_of_supply;
      state.placeOfSupply = invoice.place_of_supply;
    }

    // Rebuild the editable lines from the saved snapshots.
    state.lines = invoice.items.map((line) => ({
      key: `saved-${line.id}`,
      mode: line.item ? "item" : "custom",
      item: line.item ? { id: line.item } : null,
      item_name: line.item_name,
      item_type: line.item_type,
      hsn_sac_code: line.hsn_sac_code,
      unit: line.unit,
      service_description: line.service_description,
      quantity: line.quantity,
      unit_price: line.unit_price,
      line_discount: line.line_discount || "0.00",
      tax_rate: line.tax_rate,
      preview: {
        taxable_value: line.taxable_value,
        total_amount: line.total_amount,
      },
    }));
    // Show the stored figures immediately; the next preview confirms them.
    previewTotals = {
      totals: {
        subtotal: invoice.subtotal, total_discount: invoice.total_discount,
        taxable_total: invoice.taxable_total, cgst_total: invoice.cgst_total,
        sgst_total: invoice.sgst_total, igst_total: invoice.igst_total,
        round_off: invoice.round_off, grand_total: invoice.grand_total,
        tax_total: invoice.tax_total,
      },
      supply_type: invoice.supply_type,
      state_tax_label: invoice.state_tax_label,
      hsn_summary: [],
    };
    renderTotals();
    renderHsnSummary();
    applyLockState();
    schedulePreview();
  }

  /** An issued or cancelled invoice is read-only. */
  function applyLockState() {
    const locked = state.status !== "DRAFT";
    const controls = [
      "party-search", "party-clear", "invoice-date", "due-date", "place-of-supply",
      "prices-include-tax", "add-custom-line", "invoice-discount", "notes", "terms",
    ];
    controls.forEach((id) => { const el = $(id); if (el) el.disabled = locked; });

    $("save-draft").classList.toggle("hidden", locked);
    $("issue-btn").classList.toggle("hidden", locked);
    $("cancel-btn").classList.toggle("hidden", state.status !== "ISSUED");
    $("copy-btn").classList.toggle("hidden", false);
    $("delete-btn").classList.toggle("hidden", state.status !== "DRAFT");
    // Only a real document is worth printing; a draft is still being edited.
    $("print-btn").classList.toggle("hidden", state.status === "DRAFT");
    $("save-hint").textContent = locked
      ? "This invoice is a legal document and cannot be edited."
      : "Numbered only when you issue.";

    if (locked) {
      const banner = $("frozen-banner");
      banner.classList.remove("hidden");
      banner.textContent = state.status === "CANCELLED"
        ? `Cancelled${state.invoice.cancellation_reason ? `: ${state.invoice.cancellation_reason}` : ""}. The number is not reused.`
        : "Issued and locked. Use Cancel to void it, or Copy as new draft to re-issue.";
    }
    renderLines();
  }

  // ===================================================================
  // PARTY PICKER
  // ===================================================================
  async function selectPartyById(id, { silent = false } = {}) {
    if (!id) return;
    try {
      const party = await apiRequest(`/parties/${id}/`);
      state.party = party;
      if (!silent) {
        $("party-search").value = "";
        $("party-clear").classList.add("hidden");
      }
      renderSelectedParty();
      applyDefaultPlaceOfSupply(party);
      schedulePreview();
    } catch (err) {
      if (!silent) showFormMessage(form, err.message);
    }
  }

  /**
   * Default the place of supply from decision 4 / decision 9:
   * goods -> shipping state (fallback billing), services -> billing state,
   * walk-in -> the business's own state.
   */
  function applyDefaultPlaceOfSupply(party) {
    if (state.status !== "DRAFT") return;
    const businessState = meta.profile?.state_code || "";
    let target = party?.state_code || businessState;
    const hasGoods = state.lines.some((l) => l.item_type !== "SERVICE");
    const hasServices = state.lines.some((l) => l.item_type === "SERVICE");
    if (hasGoods && party?.shipping_state_code) target = party.shipping_state_code;
    else if (hasServices && party?.state_code) target = party.state_code;
    else if (party?.state_code) target = party.state_code;
    else if (hasGoods && party?.shipping_state_code) target = party.shipping_state_code;

    if (target) {
      $("place-of-supply").value = target;
      state.placeOfSupply = target;
    }
  }

  function renderSelectedParty() {
    const box = $("party-selected");
    if (!state.party) { box.classList.add("hidden"); return; }
    box.classList.remove("hidden");
    box.replaceChildren();

    const name = h("div", { className: "flex items-center gap-2" },
      h("span", { className: "badge badge-both" }, state.party.party_type === "CUSTOMER" ? "Customer" : state.party.party_type === "SUPPLIER" ? "Supplier" : "Both"),
      h("span", { className: "font-semibold text-navy" }, state.party.name));
    box.append(name);

    const meta2 = h("div", { className: "mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted" });
    const add = (label, value) => {
      if (value) meta2.append(h("span", {}, `${label}: `, h("strong", { className: "text-navy" }, value)));
    };
    add("GSTIN", state.party.gstin);
    add("Billing state", state.party.state_code);
    add("Shipping state", state.party.shipping_state_code);
    add("Mobile", state.party.mobile);
    box.append(meta2);
    renderPartyDetail();
  }

  function renderPartyDetail() {
    const box = $("party-detail");
    if (!state.party) { box.classList.add("hidden"); return; }
    box.classList.remove("hidden");
    const lines = [];
    if (state.party.billing_address) {
      lines.push([state.party.billing_address, state.party.billing_city, state.party.billing_pincode]
        .filter(Boolean).join(", "));
    }
    if (state.party.shipping_address) {
      lines.push(`Ships to: ${[state.party.shipping_address, state.party.shipping_city, state.party.shipping_pincode].filter(Boolean).join(", ")}`);
    }
    box.textContent = lines.join("  ·  ") || "No address on file for this party.";
  }

  async function searchParties(term) {
    const box = $("party-results");
    if (!term || term.length < 1) { box.classList.add("hidden"); return; }
    try {
      const data = await apiRequest(`/parties/?search=${encodeURIComponent(term)}&page_size=8`);
      box.replaceChildren();
      if (!data.results.length) {
        box.append(h("p", { className: "p-3 text-sm text-muted" }, "No party matches. Use “Add a new party” below."));
        box.classList.remove("hidden");
        return;
      }
      data.results.forEach((party) => {
        const option = h("button", { type: "button", className: "flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-hover", role: "option" });
        const left = h("div");
        left.append(h("div", { className: "text-sm font-medium text-navy" }, party.name));
        const bits = [party.mobile, party.gstin, party.state_code].filter(Boolean);
        if (bits.length) left.append(h("div", { className: "text-xs text-muted" }, bits.join(" · ")));
        option.append(left);
        option.append(h("span", { className: "text-xs text-muted" }, party.is_walk_in ? "Walk-in" : ""));
        option.addEventListener("click", () => {
          box.classList.add("hidden");
          selectPartyById(party.id);
        });
        box.append(option);
      });
      box.classList.remove("hidden");
    } catch {
      box.classList.add("hidden");
    }
  }

  // ===================================================================
  // LINES
  // ===================================================================
  function addItemLine(item) {
    state.lines.push({
      key: `item-${item.id}-${lineKey += 1}`,
      mode: "item",
      item: { id: item.id, name: item.name, unit: item.unit, tax_rate: item.tax_rate, item_type: item.item_type },
      item_name: item.name,
      item_type: item.item_type,
      hsn_sac_code: item.hsn_sac_code || "",
      unit: item.unit || "",
      quantity: "1.000",
      unit_price: item.sales_price,
      line_discount: "0.00",
      tax_rate: item.tax_rate,
      preview: null,
    });
    renderLines();
    applyDefaultPlaceOfSupply(state.party);
    schedulePreview();
  }

  function removeLine(key) {
    state.lines = state.lines.filter((l) => l.key !== key);
    renderLines();
    applyDefaultPlaceOfSupply(state.party);
    schedulePreview();
  }

  function lineLabel(line) {
    return line.mode === "item"
      ? (line.item?.name || line.item_name)
      : line.item_name;
  }

  function renderLines() {
    const tbody = $("lines-tbody");
    const locked = state.status !== "DRAFT";
    $("lines-wrap").classList.toggle("hidden", state.lines.length === 0);
    $("no-lines").classList.toggle("hidden", state.lines.length > 0);
    tbody.replaceChildren();

    state.lines.forEach((line) => {
      const row = h("tr");

      // --- Item cell: name + HSN/unit, or inline edit inputs for a custom line
      const nameCell = h("td");
      if (line.mode === "item") {
        nameCell.append(h("div", { className: "font-medium text-navy" }, lineLabel(line)));
        const bits = [line.hsn_sac_code, line.unit].filter(Boolean);
        if (bits.length) nameCell.append(h("div", { className: "cell-sub" }, bits.join(" · ")));
      } else {
        nameCell.append(h("div", { className: "font-medium text-navy" }, line.item_name));
        const bits = [`${ITEM_TYPE_LABELS[line.item_type] || line.item_type}`, line.hsn_sac_code, line.unit].filter(Boolean);
        if (bits.length) nameCell.append(h("div", { className: "cell-sub" }, bits.join(" · ")));
        if (line.service_description) {
          nameCell.append(h("div", { className: "cell-sub italic" }, line.service_description));
        }
      }
      row.append(nameCell);

      // --- Quantity: 3dp, because a quantity is not money (1.5 kg, not 1.50)
      row.append(h("td", {}, numberInput(line, "quantity", "0.001", locked)));
      row.append(h("td", {}, numberInput(line, "unit_price", "0.01", locked)));
      row.append(h("td", {}, numberInput(line, "line_discount", "0.01", locked)));

      // --- Tax rate (editable per line: the item default can be overridden)
      const rateCell = h("td");
      if (!locked) {
        const select = h("select", { className: "input", style: "padding: 0.35rem 0.5rem;" });
        meta.rates.forEach((r) => {
          const option = new Option(r.name, r.code);
          if (Number(r.code) === Number(line.tax_rate)) option.selected = true;
          select.append(option);
        });
        select.setAttribute("aria-label", `Tax rate for ${lineLabel(line)}`);
        select.addEventListener("change", (event) => {
          line.tax_rate = event.target.value;
          schedulePreview();
        });
        rateCell.append(select);
      } else {
        rateCell.append(h("span", { className: "text-sm" }, `${Number(line.tax_rate)}%`));
      }
      row.append(rateCell);

      // --- Computed columns. READ-ONLY, straight from the server preview.
      row.append(h("td", { className: "num" },
        line.preview ? rupees(line.preview.taxable_value) : h("span", { className: "text-muted" }, "—")));
      row.append(h("td", { className: "num amount" },
        line.preview ? rupees(line.preview.total_amount) : h("span", { className: "text-muted" }, "—")));

      // --- Remove
      const actions = h("td");
      if (!locked) {
        const remove = h("button", { type: "button", className: "icon-btn danger", ariaLabel: `Remove ${lineLabel(line)}` });
        remove.innerHTML = ICON_X; // static icon
        remove.addEventListener("click", () => removeLine(line.key));
        actions.append(remove);
      }
      row.append(actions);
      tbody.append(row);
    });
  }

  function numberInput(line, field, step, locked) {
    const input = h("input", {
      type: "number",
      step,
      min: "0",
      className: "input",
      style: "padding: 0.35rem 0.5rem; text-align: right;",
      disabled: locked,
    });
    input.value = Number(line[field] || 0).toFixed(field === "quantity" ? 3 : 2);
    input.setAttribute("aria-label", `${field.replace("_", " ")} for ${lineLabel(line)}`);
    input.addEventListener("change", (event) => {
      line[field] = event.target.value;
      schedulePreview();
    });
    return input;
  }

  // ===================================================================
  // PREVIEW - the ONLY source of every computed figure on this page
  // ===================================================================
  const schedulePreview = debounce(runPreview, PREVIEW_DEBOUNCE_MS);

  async function runPreview() {
    if (state.status !== "DRAFT") return;
    if (!state.lines.length || !state.party) {
      previewTotals = null;
      renderTotals();
      renderHsnSummary();
      return;
    }

    const ticket = ++previewTicket;
    const payload = {
      party: state.party.id,
      place_of_supply: state.placeOfSupply || undefined,
      prices_include_tax: state.priceIncludesTax,
      invoice_discount: $("invoice-discount").value || "0.00",
      items: state.lines.map((line) => {
        const entry = {
          quantity: line.quantity || "0",
          unit_price: line.unit_price || "0",
          line_discount: line.line_discount || "0",
          tax_rate: line.tax_rate,
        };
        if (line.mode === "item") entry.item = line.item.id;
        else {
          entry.item_name = line.item_name;
          entry.item_type = line.item_type;
          entry.hsn_sac_code = line.hsn_sac_code;
          entry.unit = line.unit;
          if (line.service_description) entry.service_description = line.service_description;
        }
        return entry;
      }),
    };

    try {
      const result = await apiRequest("/invoices/preview/", { method: "POST", body: payload });
      if (ticket !== previewTicket) return; // a newer keystroke already won

      previewTotals = result;
      // Attach each line's computed figures for the read-only columns.
      result.lines.forEach((computed, index) => {
        if (state.lines[index]) state.lines[index].preview = computed;
      });
      renderLines();
      renderTotals();
      renderHsnSummary();
      renderWarnings();
    } catch (err) {
      if (ticket !== previewTicket) return;
      previewTotals = null;
      renderTotals();
      renderTotalsError(err);
      renderWarnings();
    }
  }

  function renderTotalsError(err) {
    const block = $("totals-block");
    block.replaceChildren();
    // A 400 from /preview/ is a real validation message from the server - show
    // its wording rather than inventing a local explanation.
    block.append(h("p", { className: "text-sm text-danger" },
      err.message || "Could not calculate totals."));
  }

  function totalRow(label, value, { strong = false, muted = false, note = "" } = {}) {
    const row = h("div", { className: `flex items-baseline justify-between gap-4 py-1 ${strong ? "border-t border-line pt-2 font-semibold" : ""}` });
    const left = h("span", { className: muted ? "text-xs text-muted" : "text-sm text-navy" }, label);
    if (note) left.append(h("span", { className: "ml-1 text-xs text-muted" }, note));
    row.append(left, h("span", { className: strong ? "text-lg font-bold text-navy" : "text-sm text-navy" }, value));
    return row;
  }

  function renderTotals() {
    const block = $("totals-block");
    block.replaceChildren();

    if (!previewTotals) {
      block.append(h("p", { className: "text-sm text-muted" },
        state.lines.length && state.party
          ? "Calculating…"
          : "Choose a party and add at least one item to see totals."));
      return;
    }

    const t = previewTotals.totals;
    const stateLabel = previewTotals.state_tax_label;
    const taxLabel = stateLabel === "IGST" ? "IGST" : "CGST + SGST";

    block.append(totalRow("Subtotal", rupees(t.subtotal)));

    if (Number(t.total_discount) > 0) {
      block.append(totalRow(
        state.priceIncludesTax ? "Discount (incl. tax)" : "Discount",
        `− ${rupees(t.total_discount)}`,
        { muted: true },
      ));
    }

    block.append(totalRow("Taxable value", rupees(t.taxable_total)));

    if (stateLabel === "IGST") {
      block.append(totalRow("IGST", rupees(t.igst_total), { note: `at ${previewTotals.supply_type === "INTER" ? "inter-state" : ""} rate` }));
    } else {
      block.append(totalRow("CGST", rupees(t.cgst_total)));
      block.append(totalRow("SGST", rupees(t.sgst_total)));
    }

    // Round-off only when the business enabled it, so an unexpected rupee
    // difference is never silently swallowed.
    if (Number(t.round_off) !== 0) {
      block.append(totalRow("Round off", rupees(t.round_off), { muted: true }));
    }

    block.append(totalRow("Total payable", rupees(t.grand_total), { strong: true }));
    block.append(h("p", { className: "mt-1 text-right text-xs text-muted" },
      `Taxable ${rupees(t.taxable_total)} + ${taxLabel} ${rupees(t.tax_total)} = ${rupees(t.grand_total)}`));
  }

  function renderHsnSummary() {
    const rows = previewTotals?.hsn_summary || [];
    $("hsn-card").hidden = rows.length === 0;
    const tbody = $("hsn-tbody");
    tbody.replaceChildren();

    rows.forEach((row) => {
      const tr = h("tr");
      tr.append(h("td", { className: "mono" }, row.hsn_sac_code));
      tr.append(h("td", {}, row.description));
      tr.append(h("td", { className: "num" }, String(Number(row.quantity).toFixed(3))));
      tr.append(h("td", { className: "num" }, rupees(row.taxable_value)));
      tr.append(h("td", { className: "num" }, rupees(Number(row.cgst) + Number(row.sgst) + Number(row.igst))));
      tr.append(h("td", { className: "num amount" }, rupees(row.total)));
      tbody.append(tr);
    });
  }

  /**
   * Pre-submit warnings. These are ADVICE only - the server re-checks all of
   * them at issue time and returns its own error codes. The browser must never
   * be the thing that decides.
   */
  function renderWarnings() {
    const box = $("warnings");
    const warnings = [];

    if (!meta.profile?.state_code) {
      warnings.push(["warn", "Your business state is not set. You will not be able to issue an invoice until it is.",
        "/business/profile.html"]);
    }

    const chosen = $("invoice-date").value;
    if (chosen) {
      const days = Math.round((new Date() - new Date(chosen)) / 86400000);
      if (days > 0) {
        warnings.push(["warn", `This invoice is backdated by ${days} day${days === 1 ? "" : "s"} (${chosen}). Make sure that is intended.`]);
      }
    }

    const anyB2B = Boolean(state.party?.gstin)
      && meta.profile?.gst_registration_type === "REGULAR";
    if (anyB2B) {
      const missing = state.lines.filter((l) => !l.hsn_sac_code);
      if (missing.length) {
        warnings.push(["error", `${missing.length} line${missing.length === 1 ? "" : "s"} still need${missing.length === 1 ? "s" : ""} an HSN/SAC code before this can be issued.`]);
      }
    }

    if (state.lines.length === 0) {
      warnings.push(["warn", "Add at least one item before saving."]);
    }

    box.replaceChildren(...warnings.map(([kind, text, href]) => {
      const node = h("div", {
        className: `flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
          kind === "error" ? "border-red-200 bg-red-50 text-red-800"
            : "border-amber-300 bg-amber-50 text-amber-800"}`,
      });
      const icon = h("span", { className: "mt-0.5 shrink-0" });
      icon.innerHTML = ICON_SUGGEST; // static icon
      node.append(icon, h("span", {}, text));
      if (href) node.append(h("a", { href, className: "ml-1 underline shrink-0" }, "Fix"));
      return node;
    }));
  }

  // ===================================================================
  // SAVE / ISSUE
  // ===================================================================
  function buildPayload() {
    return {
      party: state.party.id,
      invoice_date: $("invoice-date").value,
      due_date: $("due-date").value || null,
      place_of_supply: $("place-of-supply").value,
      prices_include_tax: $("prices-include-tax").checked,
      invoice_discount: $("invoice-discount").value || "0.00",
      notes: $("notes").value,
      terms: $("terms").value,
      items: state.lines.map((line) => {
        const entry = {
          quantity: line.quantity,
          unit_price: line.unit_price,
          line_discount: line.line_discount || "0.00",
          tax_rate: line.tax_rate,
        };
        if (line.mode === "item") entry.item = line.item.id;
        else {
          entry.item_name = line.item_name;
          entry.item_type = line.item_type;
          entry.hsn_sac_code = line.hsn_sac_code;
          entry.unit = line.unit;
          if (line.service_description) entry.service_description = line.service_description;
        }
        return entry;
      }),
    };
  }

  async function saveDraft() {
    clearErrors(form);
    const payload = buildPayload();
    if (state.invoiceId) {
      const invoice = await apiRequest(`/invoices/${state.invoiceId}/`, { method: "PATCH", body: payload });
      showToast("Draft saved.");
      return invoice;
    }
    const invoice = await apiRequest("/invoices/", { method: "POST", body: payload });
    // Keep the URL shareable, so a refresh does not create a second draft.
    window.history.replaceState({}, "", `/invoices/billing.html?id=${invoice.id}`);
    showToast("Draft saved.");
    return invoice;
  }

  async function onSubmit(event) {
    event.preventDefault();
    if (!state.party) {
      clearErrors(form);
      const el = form.querySelector('[data-error-for="party"]');
      if (el) el.textContent = "Choose a party first.";
      $("party-search").focus();
      return;
    }
    if (!state.lines.length) {
      clearErrors(form);
      const el = form.querySelector('[data-error-for="items"]');
      if (el) el.textContent = "Add at least one item.";
      return;
    }

    setBusy($("save-draft"), true, "Saving...");
    try {
      await saveDraft();
    } catch (err) {
      showFieldErrors(form, err);
    } finally {
      setBusy($("save-draft"), false);
    }
  }

  async function onIssue() {
    if (!state.party || !state.lines.length) {
      showFormMessage(form, "Choose a party and add at least one item first.");
      return;
    }
    clearErrors(form);
    // Disabled immediately, before any await, so a double-click cannot send two
    // issue requests. (The backend is idempotent anyway - it returns the same
    // number rather than burning a second one - but there is no reason to ask.)
    const button = $("issue-btn");
    button.disabled = true;
    setBusy(button, true, "Issuing…");
    try {
      const saved = await saveDraft();
      const issued = await apiRequest(`/invoices/${saved.id}/issue/`, { method: "POST", body: {} });
      showToast(`Issued ${issued.display_number}`);
      window.location.href = "/invoices/index.html";
    } catch (err) {
      const hints = {
        BUSINESS_PROFILE_INCOMPLETE: "Set your business state in Business profile first.",
        PARTY_STATE_MISSING: "This party has no billing state. Set it, then re-issue.",
        HSN_REQUIRED: "Every line needs an HSN/SAC code. Add them, then issue.",
        HSN_INVALID: "One of the HSN/SAC codes is not valid.",
        INVOICE_EMPTY: "Add at least one line, then issue.",
        SERIES_EXHAUSTED: "This financial year's invoice series is full. Contact support.",
        NUMBER_RACE: "Another session just issued this invoice. Reload to see it.",
      };
      showFormMessage(form, hints[err.code] || err.message);
      showFieldErrors(form, err);
      button.disabled = false;
      setBusy(button, false);
    }
  }

  async function onCancelInvoice() {
    const dialog = $("cancel-invoice-dialog");
    $("cancel-invoice-number").textContent = state.invoice?.display_number || "";
    $("cancel-invoice-reason").value = "";
    dialog.hidden = false;
    $("cancel-invoice-reason").focus();
  }

  async function confirmCancelInvoice() {
    const reason = $("cancel-invoice-reason").value.trim();
    const errorEl = $("cancel-invoice-dialog").querySelector('[data-error-for="reason"]');
    errorEl.textContent = "";
    if (!reason) {
      errorEl.textContent = "A reason is required - it is printed on the cancellation record.";
      return;
    }
    const button = $("cancel-invoice-confirm");
    setBusy(button, true, "Cancelling...");
    try {
      await apiRequest(`/invoices/${state.invoiceId}/cancel/`, { method: "POST", body: { reason } });
      $("cancel-invoice-dialog").hidden = true;
      showToast("Invoice cancelled. Its number is not reused.");
      window.location.reload();
    } catch (err) {
      errorEl.textContent = err.message;
    } finally {
      setBusy(button, false);
    }
  }

  async function onCopy() {
    const button = $("copy-btn");
    setBusy(button, true, "Copying…");
    try {
      const copy = await apiRequest(`/invoices/${state.invoiceId}/copy/`, { method: "POST", body: {} });
      showToast("Copied to a new draft.");
      window.location.href = `/invoices/billing.html?id=${copy.id}`;
    } catch (err) {
      showToast(err.message, "error");
      setBusy(button, false);
    }
  }

  async function onDelete() {
    if (!window.confirm("Delete this draft? This cannot be undone.")) return;
    const button = $("delete-btn");
    setBusy(button, true, "Deleting...");
    try {
      await apiRequest(`/invoices/${state.invoiceId}/`, { method: "DELETE" });
      window.location.href = "/invoices/index.html";
    } catch (err) {
      showToast(err.message, "error");
      setBusy(button, false);
    }
  }

  // ===================================================================
  // CUSTOM LINE DIALOG
  // ===================================================================
  function openCustomDialog() {
    const dialog = $("custom-dialog");
    $("custom-name").value = "";
    $("custom-hsn").value = "";
    $("custom-desc").value = "";
    $("custom-qty").value = "1";
    $("custom-price").value = "0.00";
    $("custom-type").value = "PRODUCT";
    $("custom-desc-wrap").hidden = true;
    $("custom-hsn-hint").textContent = "4, 6 or 8 digits.";
    const msg = dialog.querySelector("[data-custom-message]");
    msg.textContent = "";
    msg.classList.add("hidden");
    dialog.hidden = false;
    $("custom-name").focus();
  }

  function onCustomTypeChange() {
    const isService = $("custom-type").value === "SERVICE";
    $("custom-desc-wrap").hidden = !isService;
    $("custom-hsn-hint").textContent = isService
      ? "SAC code: 4 or 6 digits (services never use 8)."
      : "HSN code: 4, 6 or 8 digits (5 and 7 are not used).";
  }

  function addCustomLine() {
    const dialog = $("custom-dialog");
    const msg = dialog.querySelector("[data-custom-message]");
    const fail = (text) => {
      msg.textContent = text;
      msg.className = "rounded p-3 text-sm bg-red-50 text-red-700";
    };

    const itemType = $("custom-type").value;
    const hsn = $("custom-hsn").value.trim();
    const description = $("custom-desc").value.trim();
    const name = $("custom-name").value.trim();
    const unit = $("custom-unit").value;
    const quantity = $("custom-qty").value;
    const unitPrice = $("custom-price").value;

    // Mirror the server's rules so the user gets an instant answer, but the
    // server still validates: this is convenience, not authority.
    const pattern = itemType === "SERVICE" ? /^(?:[0-9]{4}|[0-9]{6})$/ : /^(?:[0-9]{4}|[0-9]{6}|[0-9]{8})$/;
    if (!name) return fail("Give the line a description.");
    if (!hsn) return fail("An HSN/SAC code is required on every line.");
    if (!pattern.test(hsn)) {
      return fail(itemType === "SERVICE"
        ? "A SAC code must be 4 or 6 digits."
        : "An HSN code must be 4, 6 or 8 digits (5 and 7 are not used).");
    }
    if (itemType === "SERVICE" && !description) return fail("Services need a description - it is printed on the tax invoice.");
    if (!unit) return fail("Choose a unit.");
    if (!(Number(quantity) > 0)) return fail("Quantity must be greater than zero.");
    if (!(Number(unitPrice) >= 0) || unitPrice === "") return fail("Enter a rate.");

    state.lines.push({
      key: `custom-${lineKey += 1}`,
      mode: "custom",
      item: null,
      item_name: name,
      item_type: itemType,
      hsn_sac_code: hsn,
      unit,
      service_description: description,
      quantity,
      unit_price: unitPrice,
      line_discount: "0.00",
      tax_rate: $("custom-rate").value,
      preview: null,
    });

    dialog.hidden = true;
    renderLines();
    applyDefaultPlaceOfSupply(state.party);
    schedulePreview();
  }

  // ===================================================================
  // ITEM SEARCH (inline, above the lines table)
  // ===================================================================
  async function searchItems(term) {
    const list = $("item-results");
    if (!term) { list.classList.add("hidden"); return; }
    try {
      const data = await apiRequest(`/items/?search=${encodeURIComponent(term)}&page_size=8`);
      list.replaceChildren();
      if (!data.results.length) {
        list.append(h("p", { className: "p-3 text-sm text-muted" }, "No item matches. Use “+ Custom line” instead."));
        list.classList.remove("hidden");
        return;
      }
      data.results.forEach((item) => {
        const option = h("button", { type: "button", className: "flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-hover" });
        const left = h("div");
        left.append(h("div", { className: "text-sm font-medium text-navy" }, item.name));
        const bits = [item.item_code, item.hsn_sac_code, item.unit].filter(Boolean);
        if (bits.length) left.append(h("div", { className: "text-xs text-muted" }, bits.join(" · ")));
        option.append(left);
        option.append(h("span", { className: "text-sm font-semibold text-navy" }, rupees(item.sales_price)));
        option.addEventListener("click", () => {
          list.classList.add("hidden");
          $("item-search").value = "";
          addItemLine(item);
        });
        list.append(option);
      });
      list.classList.remove("hidden");
    } catch {
      list.classList.add("hidden");
    }
  }

  // ===================================================================
  // EVENTS
  // ===================================================================
  function wireEvents() {
    $("party-search").addEventListener("input", debounce((event) => searchParties(event.target.value.trim()), 250));
    $("party-clear").addEventListener("click", () => {
      state.party = null;
      $("party-search").value = "";
      $("party-clear").classList.add("hidden");
      $("party-selected").classList.add("hidden");
      $("party-detail").classList.add("hidden");
      setDefaultPlaceOfSupply();
      schedulePreview();
    });

    $("quick-add-party").addEventListener("click", () => {
      window.location.href = "/parties/index.html?new=1";
    });

    $("item-search").addEventListener("input", debounce((event) => {
      searchItems(event.target.value.trim());
    }, 250));
    $("item-search").addEventListener("blur", () => {
      // Let a click on a result land before the list disappears.
      setTimeout(() => $("item-results").classList.add("hidden"), 150);
    });

    $("place-of-supply").addEventListener("change", (event) => {
      state.placeOfSupply = event.target.value;
      schedulePreview();
    });

    $("prices-include-tax").addEventListener("change", (event) => {
      state.priceIncludesTax = event.target.checked;
      schedulePreview();
    });

    ["invoice-date", "due-date"].forEach((id) => $(id).addEventListener("change", renderWarnings));
    $("invoice-discount").addEventListener("input", () => schedulePreview());
    $("round-total").addEventListener("change", schedulePreview);

    $("add-custom-line").addEventListener("click", openCustomDialog);
    $("custom-type").addEventListener("change", onCustomTypeChange);
    $("custom-add").addEventListener("click", addCustomLine);
    $("custom-dialog").querySelectorAll("[data-close]").forEach((el) => {
      el.addEventListener("click", () => { $("custom-dialog").hidden = true; });
    });

    $("cancel-invoice-dialog").querySelectorAll("[data-close]").forEach((el) => {
      el.addEventListener("click", () => { $("cancel-invoice-dialog").hidden = true; });
    });
    $("cancel-invoice-confirm").addEventListener("click", confirmCancelInvoice);

    form.addEventListener("submit", onSubmit);
    $("issue-btn").addEventListener("click", onIssue);
    $("cancel-btn").addEventListener("click", onCancelInvoice);
    $("copy-btn").addEventListener("click", onCopy);
    $("delete-btn").addEventListener("click", onDelete);
    $("print-btn").addEventListener("click", () => window.print());

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      ["custom-dialog", "cancel-invoice-dialog"].forEach((id) => {
        if (!$(id).hidden) $(id).hidden = true;
      });
    });
  }

  wireEvents();
  boot();
})();
