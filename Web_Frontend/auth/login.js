/** Login page logic. */
redirectIfLoggedIn();

const form = document.getElementById("login-form");
const button = form.querySelector("button[type=submit]");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearErrors(form);
  setBusy(button, true, "Logging in... (the first request can take a minute)");
  try {
    const tokens = await apiRequest("/auth/login/", {
      method: "POST",
      auth: false,
      body: { email: form.email.value.trim(), password: form.password.value },
    });
    Auth.save(tokens);
    window.location.href = "/dashboard/";
  } catch (err) {
    showFieldErrors(form, err);
    setBusy(button, false);
  }
});