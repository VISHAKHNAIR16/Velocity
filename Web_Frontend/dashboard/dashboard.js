/** Dashboard page logic. */
(async () => {
  if (!requireLogin()) return;
  renderNav("dashboard");
  try {
    const profile = await apiRequest("/business/profile/");
    document.getElementById("welcome").textContent = `Welcome, ${profile.trade_name}`;
    if (!profile.is_complete) document.getElementById("setup-banner").classList.remove("hidden");
  } catch (err) {
    document.getElementById("welcome").textContent = err.message;
  }
})();