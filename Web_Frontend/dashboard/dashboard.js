/** Dashboard page logic. Renders metric cards, low-stock alerts, recent payments. */
(async () => {
  if (!requireLogin()) return;
  renderNav("dashboard");

  const welcomeEl = document.getElementById("welcome");
  const welcomeSubEl = document.getElementById("welcome-sub");

  try {
    const profile = await apiRequest("/business/profile/");
    welcomeEl.textContent = `Welcome, ${profile.trade_name}`;
    if (!profile.is_complete) {
      document.getElementById("setup-banner").classList.remove("hidden");
    }
  } catch (err) {
    welcomeEl.textContent = err.message;
  }

  // ---- Metric cards ----
  const metricCardsEl = document.getElementById("metric-cards");
  try {
    const stats = await apiRequest("/api/v1/dashboard/stats/");
    metricCardsEl.innerHTML = `
      <div class="card rounded-lg border border-slate-200 bg-white p-6">
        <div class="text-xs font-medium text-slate-500 uppercase tracking-wider">Total Sales</div>
        <div class="mt-1 text-2xl font-bold text-navy">₹${formatINR(Number(stats.total_sales))}</div>
        <div class="mt-1 text-xs text-slate-500">This month: ₹${formatINR(Number(stats.total_sales_this_month))}</div>
      </div>
      <div class="card rounded-lg border border-slate-200 bg-white p-6">
        <div class="text-xs font-medium text-slate-500 uppercase tracking-wider">Total Purchases</div>
        <div class="mt-1 text-2xl font-bold text-navy">₹${formatINR(Number(stats.total_purchases))}</div>
        <div class="mt-1 text-xs text-amber-600">${stats.total_purchases_note}</div>
      </div>
      <div class="card rounded-lg border border-slate-200 bg-white p-6">
        <div class="text-xs font-medium text-slate-500 uppercase tracking-wider">Total Receivables</div>
        <div class="mt-1 text-2xl font-bold text-amber-600">₹${formatINR(Number(stats.total_receivables))}</div>
        <div class="mt-1 text-xs text-slate-500">Money owed by customers</div>
      </div>
      <div class="card rounded-lg border border-slate-200 bg-white p-6">
        <div class="text-xs font-medium text-slate-500 uppercase tracking-wider">Total Payables</div>
        <div class="mt-1 text-2xl font-bold text-navy">₹${formatINR(Number(stats.total_payables))}</div>
        <div class="mt-1 text-xs text-amber-600">${stats.total_payables_note}</div>
      </div>
      <div class="card rounded-lg border border-slate-200 bg-white p-6">
        <div class="text-xs font-medium text-slate-500 uppercase tracking-wider">Estimated Net Profit</div>
        <div class="mt-1 text-2xl font-bold text-navy">₹${formatINR(Number(stats.estimated_net_profit))}</div>
        <div class="mt-1 text-xs text-slate-500">${stats.estimated_net_profit_note}</div>
      </div>
    `;
  } catch (err) {
    metricCardsEl.innerHTML = `
      <div class="card rounded-lg border border-slate-200 bg-white p-6 text-slate-500 text-sm">
        Could not load dashboard stats. ${err.message}
      </div>
    `;
  }

  // ---- Low-stock alerts ----
  const lowStockBody = document.getElementById("low-stock-body");
  const lowStockEmpty = document.getElementById("low-stock-empty");
  try {
    const lowStock = await apiRequest("/api/v1/dashboard/low-stock/");
    if (lowStock.length === 0) {
      lowStockEmpty.classList.remove("hidden");
    } else {
      lowStockBody.innerHTML = lowStock.map(item => `
        <tr class="border-b border-slate-100">
          <td class="px-4 py-3">${escapeHtml(item.name)}</td>
          <td class="px-4 py-3 text-slate-500">${escapeHtml(item.item_code || "-")}</td>
          <td class="px-4 py-3 font-medium text-red-600">${item.current_stock !== null ? item.current_stock : "—"}</td>
          <td class="px-4 py-3 text-slate-500">${item.low_stock_threshold !== null ? item.low_stock_threshold : "—"}</td>
          <td class="px-4 py-3">
            <span class="inline-flex items-center rounded-full px-2 py-1 text-xs font-medium bg-red-100 text-red-700">
              Below threshold
            </span>
          </td>
        </tr>
      `).join("");
    }
  } catch (err) {
    lowStockBody.innerHTML = `
      <tr><td colspan="5" class="px-4 py-3 text-slate-500">Could not load low-stock items.</td></tr>
    `;
  }

  // ---- Recent payments ----
  const recentPaymentsBody = document.getElementById("recent-payments-body");
  const recentPaymentsEmpty = document.getElementById("recent-payments-empty");
  try {
    const payments = await apiRequest("/api/v1/payments/?ordering=-payment_date&limit=5");
    const rows = Array.isArray(payments) ? payments : (payments.results || []);
    if (rows.length === 0) {
      recentPaymentsEmpty.classList.remove("hidden");
    } else {
      recentPaymentsBody.innerHTML = rows.map(p => {
        const isIn = p.transaction_type === "PAYMENT_IN";
        return `
          <tr class="border-b border-slate-100">
            <td class="px-4 py-3 text-slate-600">${new Date(p.payment_date).toLocaleDateString("en-IN")}</td>
            <td class="px-4 py-3 font-medium">${escapeHtml(p.party_name)}</td>
            <td class="px-4 py-3">
              <span class="inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ${isIn ? "bg-green-100 text-green-700" : "bg-blue-100 text-blue-700"}">
                ${isIn ? "Received" : "Paid"}
              </span>
            </td>
            <td class="px-4 py-3 font-medium text-navy">₹${formatINR(Number(p.amount))}</td>
            <td class="px-4 py-3 text-slate-500 text-capitalize">${p.payment_mode.replace("_", " ")}</td>
            <td class="px-4 py-3 text-slate-500">${escapeHtml(p.reference_number || "-")}</td>
          </tr>
        `;
      }).join("");
    }
  } catch (err) {
    recentPaymentsBody.innerHTML = `
      <tr><td colspan="6" class="px-4 py-3 text-slate-500">Could not load recent payments.</td></tr>
    `;
  }
})();

/** Escape HTML to prevent XSS when rendering user-provided text. */
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}
