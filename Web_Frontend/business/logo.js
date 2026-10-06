/**
 * Logo upload widget for the business profile page.
 * Requires api.js and ui.js. profile.js calls logoShow() when the profile loads.
 */
const LOGO_MAX_BYTES = 2 * 1024 * 1024;
const LOGO_TYPES = ["image/png", "image/jpeg", "image/webp"];

const logoImg = document.getElementById("logo-preview");
const logoEmpty = document.getElementById("logo-placeholder");
const logoFile = document.getElementById("logo-file");
const logoChoose = document.getElementById("logo-choose");
const logoRemove = document.getElementById("logo-remove");
const logoMessage = document.getElementById("logo-message");

/** Show the logo (url) or the empty placeholder (null). */
function logoShow(url) {
  logoImg.classList.toggle("hidden", !url);
  logoEmpty.classList.toggle("hidden", Boolean(url));
  logoRemove.classList.toggle("hidden", !url);
  if (url) logoImg.src = url;
}

function logoSay(text, ok = false) {
  logoMessage.textContent = text;
  logoMessage.className = "mt-1 text-sm " + (ok ? "text-green-600" : "text-red-600");
}

logoChoose.addEventListener("click", () => logoFile.click());

logoFile.addEventListener("change", async () => {
  const file = logoFile.files[0];
  logoFile.value = ""; // allows choosing the same file again later
  if (!file) return;

  // Quick checks for instant feedback. The server re-checks everything.
  if (!LOGO_TYPES.includes(file.type)) return logoSay("Please choose a PNG, JPG or WebP image.");
  if (file.size > LOGO_MAX_BYTES) return logoSay("The image must be 2 MB or smaller.");

  setBusy(logoChoose, true, "Uploading...");
  logoSay("");
  try {
    const data = new FormData();
    data.append("logo", file);
    const profile = await apiRequest("/business/logo/", { method: "PUT", body: data });
    logoShow(profile.logo);
    logoSay("Logo updated.", true);
  } catch (err) {
    logoSay((err.errors.logo || [err.message]).join(" "));
  } finally {
    setBusy(logoChoose, false);
  }
});

logoRemove.addEventListener("click", async () => {
  try {
    await apiRequest("/business/logo/", { method: "DELETE" });
    logoShow(null);
    logoSay("Logo removed.", true);
  } catch (err) {
    logoSay(err.message);
  }
});