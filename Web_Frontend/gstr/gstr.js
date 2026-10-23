/**
 * GSTR Reports page: view and export GSTR-1 / GSTR-3B summaries.
 * Requires config.js, api.js, ui.js.
 */
(() => {
  "use strict";

  const state = {
    period: "",
    reportType: "gstr1",
    format: "json",
  };

  const $ = (id) => document.getElementById(id);
  const periodSelect = $("period-select");
  const reportTypeSelect = $("report-type");
  const formatSelect = $("export-format");
  const reportContainer = $("report-container");
  const reportBody = $("report-body");
  const loadingState = $("loading-state");
  const errorState = $("error-state");

  function generatePeriods() {
    const now = new Date();
    const months = [];
    for (let i = 0; i < 12; i++) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      months.push({ value: `${y}-${m}`, label: `${y}-${m}` });
    }
    return months;
  }

  function populatePeriods() {
    const periods = generatePeriods();
    periodSelect.replaceChildren();
    periods.forEach((p) => periodSelect.append(new Option(p.label, p.value)));
    periodSelect.value = periods[0].value;
    state.period = periods[0].value;
  }

  function showReport() {
    loadingState.hidden = true;
    errorState.hidden = true;
    reportContainer.hidden = false;
  }

  function showError(message) {
    loadingState.hidden = true;
    errorState.hidden = true;
    reportContainer.hidden = true;
    $("error-text").textContent = message || "Something went wrong.";
    errorState.hidden = false;
  }

  function showLoading() {
    loadingState.hidden = false;
    errorState.hidden = true;
    reportContainer.hidden = true;
  }

  async function loadReport() {
    showLoading();
    try {
      const params = new URLSearchParams({ period: state.period });
      const url = state.reportType === "gstr1" ? "/gstr1/" : "/gstr3b/";
      if (state.format === "json") {
        const data = await apiRequest(`${url}?${params}`);
        renderReport(data);
        showReport();
      } else {
        const endpoint = state.format === "csv" ? "export/?format=csv" : "export/?format=xlsx";
        const resp = await fetch(`${API_BASE_URL}${url}?${params}&format=${state.format === "csv" ? "csv" : "xlsx"}`, {
          headers: { Authorization: `Bearer ${localStorage.getItem("velocity_access") || ""}` },
        });
        if (!resp.ok) throw new Error("Export failed");
        const blob = await resp.blob();
        const url2 = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url2;
        a.download = `${state.reportType.toUpperCase()}_${state.period}.${state.format === "xlsx" ? "xlsx" : "csv"}`;
        a.click();
        window.URL.revokeObjectURL(url2);
        loadingState.hidden = true;
      }
    } catch (err) {
      showError(err.message);
    }
  }

  function renderReport(data) {
    reportBody.replaceChildren();
    if (state.reportType === "gstr1") {
      const sections = [
        { key: "b2b_total", label: "B2B Total", val: data.b2b_total },
        { key: "b2c_large_total", label: "B2C Large Total", val: data.b2c_large_total },
        { key: "b2c_small_total", label: "B2C Small Total", val: data.b2c_small_total },
        { key: "grand_total", label: "Grand Total", val: data.grand_total },
      ];
      sections.forEach((s) => {
        reportBody.append(h("tr", { className: "divide-y divide-line-soft" }).appendChild(
          h("td", { className: "px-4 py-3 font-medium text-navy", textContent: s.label }) && h("td")
        ) && reportBody);
        reportBody.lastChild.append(h("td", { className: "px-4 py-3 text-right tabular-nums text-navy", textContent: `₹${formatINR(Number(s.val))}` }));
      });
    } else {
      const rows = [
        { label: "Outward Taxable Value", val: data.outward_supplies.taxable_value },
        { label: "Outward CGST", val: data.outward_supplies.cgst },
        { label: "Outward SGST", val: data.outward_supplies.sgst },
        { label: "Outward IGST", val: data.outward_supplies.igst },
        { label: "Outward Tax Total", val: data.outward_supplies.tax_total },
        { label: "ITC Taxable Value", val: data.input_tax_credit.taxable_value },
        { label: "ITC CGST", val: data.input_tax_credit.cgst },
        { label: "ITC SGST", val: data.input_tax_credit.sgst },
        { label: "ITC IGST", val: data.input_tax_credit.igst },
        { label: "ITC Tax Total", val: data.input_tax_credit.tax_total },
        { label: "Net CGST Liability", val: data.net_tax_liability.cgst },
        { label: "Net SGST Liability", val: data.net_tax_liability.sgst },
        { label: "Net IGST Liability", val: data.net_tax_liability.igst },
        { label: "Net Tax Liability", val: data.net_tax_liability.tax_total },
      ];
      rows.forEach((r) => {
        reportBody.append(h("tr", { className: "divide-y divide-line-soft" }).appendChild(
          h("td", { className: "px-4 py-3 text-slate-600", textContent: r.label }) && h("td")
        ) && reportBody);
        reportBody.lastChild.append(h("td", { className: "px-4 py-3 text-right tabular-nums text-navy", textContent: `₹${formatINR(Number(r.val))}` }));
      });
    }
  }

  function bindEvents() {
    $("load-report-btn").addEventListener("click", () => {
      state.period = periodSelect.value;
      state.reportType = reportTypeSelect.value;
      state.format = formatSelect.value;
      loadReport();
    });
    periodSelect.addEventListener("change", (e) => { state.period = e.target.value; });
    reportTypeSelect.addEventListener("change", (e) => { state.reportType = e.target.value; });
    formatSelect.addEventListener("change", (e) => { state.format = e.target.value; });
    $("retry-btn").addEventListener("click", loadReport);
  }

  if (!requireLogin()) return;
  renderNav("gstr");
  populatePeriods();
  bindEvents();
})();
