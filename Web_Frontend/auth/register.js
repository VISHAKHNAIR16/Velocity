/** Registration page logic. */
redirectIfLoggedIn();

const form = document.getElementById("register-form");
const button = form.querySelector("button[type=submit]");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearErrors(form);

  // Typo guard only. The real password rules are enforced by the backend.
  if (form.password.value !== form.password2.value) {
    form.querySelector('[data-error-for="password2"]').textContent = "Passwords do not match.";
    return;
  }

  setBusy(button, true, "Creating account... (the first request can take a minute)");
  try {
    const tokens = await apiRequest("/auth/register/", {
      method: "POST",
      auth: false,
      body: {
        email: form.email.value.trim(),
        password: form.password.value,
        trade_name: form.trade_name.value.trim(),
      },
    });
    Auth.save(tokens);
    window.location.href = "/business/profile.html"; // next: complete the business details
  } catch (err) {
    showFieldErrors(form, err);
    setBusy(button, false);
  }
});