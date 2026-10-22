/**
 * Party Statement page: running-balance ledger for one party.
 * Accepts ?party=ID in the URL. Requires config.js, api.js, ui.js.
 */
(() => {
  "use strict";

  const urlParams = new URLSearchParams(window.location.search);
  const partyId = urlParams.get("party");

  if (!partyId) {
    document.body.innerHTML = "<p class='p-8 text-red-600'>No party specified. Use ?party=ID</p>";
    return;
  }

  const $ = (id) => document.getElementById(id);

  const partySummary = $("party-summary");
  const stmtBody = $("stmt-body");
  const stmtEmpty = $("stmt-empty");
  const stmtContainer = $("stmt-container");

  // Totals
  const stmtTotalDebit = $("stmt-total-debit");
  const stmtTotalCredit = $("stmt-total-credit");
  const stmtFinalBalance = $("stmt-final-balance");

  // Party summary
  const stmtPartyName = $("stmt-party-name");
  const stmtPartyType = $("stmt-party-type");
  const stmtOpening = $("stmt-opening");
  const stmtBilled = $("stmt-billed");
  const stmtPaid = $("stmt-paid");
  const stmtPending = $("stmt-pending");

  // Filters
  const stmtDateFrom = $("stmt-date-from");
  const stmtDateTo = $("stmt-date-to");
  const stmtApply = $("stmt-apply-filters");
  const stmtClear = $("stmt-clear-filters");

  let state = {
    start_date: "",
    end_date: "",
  };

  // ===================================================================
  // LOAD
  // ===================================================================
  async function loadStatement() {
    try {
      const params = new URLSearchParams();
      if (state.start_date) params.set("start_date", state.start_date);
      if (state.end_date) params.set("end_date", state.end_date);

      const data = await apiRequest(`/parties/${partyId}/statement/?${params}`);
      render(data);
    } catch (err) {
      document.body.insertAdjacentHTML("afterbegin",
        `<div class="p-4 rounded bg-red-50 border border-red-200 text-red-700">${escapeHtml(err.message)}</div>`);
    }
  }

  function render(data) {
    const party = data.party;
    stmtPartyName.textContent = party.name;
    stmtPartyType.textContent = `${party.party_type === "CUSTOMER" ? "Customer" : party.party_type === "SUPPLIER" ? "Supplier" : "Both"} · ${party.opening_balance_signed >= 0 ? "party owes us" : "we owe party"}`;
    stmtOpening.textContent = `₹${formatINR(Number(party.opening_balance_signed))}`;
    stmtBilled.textContent = `₹${formatINR(Number(data.totals.total_billed))}`;
    stmtPaid.textContent = `₹${formatINR(Number(data.totals.total_paid))}`;
    stmtPending.textContent = `₹${formatINR(Number(data.totals.pending_balance))}`;

    const entries = data.entries || [];
    stmtTotalDebit.textContent = `₹${formatINR(Number(data.totals.total_billed))}`;
    stmtTotalCredit.textContent = `₹${formatINR(Number(data.totals.total_paid))}`;
    stmtFinalBalance.textContent = `₹${formatINR(Number(data.totals.pending_balance))}`;

    partySummary.classList.remove("hidden");

    if (entries.length === 0) {
      stmtContainer.classList.add("hidden");
      stmtEmpty.classList.remove("hidden");
      return;
    }

    stmtEmpty.classList.add("hidden");
    stmtContainer.classList.remove("hidden");

    stmtBody.replaceChildren(...entries.map(buildRow));
  }

  function buildRow(entry) {
    const isDebit = entry.type === "SALES_INVOICE";
    const tr = h("tr", { className: "transition-colors hover:bg-hover" });

    tr.append(h("td", {
      className: "px-4 py-3 text-sm text-navy tabular-nums",
      textContent: new Date(entry.date).toLocaleDateString("en-IN"),
    }));

    tr.append(h("td", {
      className: "px-4 py-3 hidden sm:table-cell",
      textContent: entry.type === "SALES_INVOICE" ? "Sales Invoice" : "Payment Received",
    }));

    tr.append(h("td", {
      className: "px-4 py-3 text-sm text-slate-500 hidden md:table-cell",
      textContent: entry.reference,
    }));

    tr.append(h("td", {
      className: "px-4 py-3 text-right tabular-nums",
      textContent: isDebit ? `₹${formatINR(Number(entry.debit))}` : "—",
    }));

    tr.append(h("td", {
      className: "px-4 py-3 text-right tabular-nums",
      textContent: !isDebit ? `₹${formatINR(Number(entry.credit))}` : "—",
    }));

    tr.append(h("td", {
      className: "px-4 py-3 text-right font-medium tabular-nums",
      textContent: `₹${formatINR(Number(entry.balance))}`,
    }));

    return tr;
  }

  // ===================================================================
  // FILTERS
  // ===================================================================
  stmtApply.addEventListener("click", () => {
    state.start_date = stmtDateFrom.value;
    state.end_date = stmtDateTo.value;
    loadStatement();
  });

  stmtClear.addEventListener("click", () => {
    stmtDateFrom.value = "";
    stmtDateTo.value = "";
    state.start_date = "";
    state.end_date = "";
    loadStatement();
  });

  // ===================================================================
  // PRINT
  // ===================================================================
  $("print-btn").addEventListener("click", () => {
    window.print();
  });

  // ===================================================================
  // START
  // ===================================================================
  if (!requireLogin()) return;
  renderNav("parties");
  loadStatement();
})();

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}
