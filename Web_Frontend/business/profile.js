/** Business profile page logic. */

// Form layout. To add a field later, add one line here (and the backend field).
const SECTIONS = [
  { title: "Business details", fields: [
    { name: "trade_name", label: "Trade / brand name", required: true },
    { name: "company_name", label: "Company name (legal)", required: true },
    { name: "owner_name", label: "Owner name" },
    { name: "phone", label: "Company phone", type: "tel", maxlength: 10, hint: "10-digit mobile number" },
    { name: "alternate_phone", label: "Alternative contact number", type: "tel", maxlength: 15 },
    { name: "email", label: "Company email", type: "email" },
    { name: "website", label: "Website", placeholder: "www.example.com" },
    { name: "gstin", label: "GSTIN", maxlength: 15, upper: true, placeholder: "36ABCCS2942R1ZR" },
    { name: "pan", label: "PAN number", maxlength: 10, upper: true },
  ]},
  { title: "Billing address", fields: [
    { name: "address_line", label: "Address" },
    { name: "city", label: "City" },
    { name: "state_code", label: "State", type: "select" },
    { name: "pincode", label: "Pincode", maxlength: 6 },
  ]},
  { title: "Bank details", fields: [
    { name: "bank_account_name", label: "Account holder name" },
    { name: "bank_account_number", label: "Account number", maxlength: 18 },
    { name: "bank_ifsc", label: "IFSC code", maxlength: 11, upper: true },
    { name: "bank_name", label: "Bank name" },
    { name: "bank_branch", label: "Branch" },
  ]},
];
const ALL_FIELDS = SECTIONS.flatMap((s) => s.fields);

const form = document.getElementById("profile-form");
const saveButton = form.querySelector("button[type=submit]");

/** Create a small element with optional class and text. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

/** Build one label + input/select + error line. */
function buildField(f) {
  const wrap = el("div");
  const label = el("label", "label", f.label + (f.required ? " *" : ""));
  label.htmlFor = f.name;

  const input = f.type === "select" ? el("select", "input") : el("input", "input");
  input.id = f.name;
  input.name = f.name;
  if (f.type === "select") {
    input.append(new Option("Select state", ""));
  } else {
    input.type = f.type || "text";
    if (f.maxlength) input.maxLength = f.maxlength;
    if (f.placeholder) input.placeholder = f.placeholder;
    if (f.upper) input.classList.add("uppercase"); // display only; the backend stores upper case
  }

  wrap.append(label, input);
  if (f.hint) wrap.append(el("p", "hint", f.hint));
  const error = el("p", "field-error");
  error.dataset.errorFor = f.name;
  wrap.append(error);
  return wrap;
}

/** Draw all sections into the page. */
function buildForm() {
  const container = document.getElementById("sections");
  for (const section of SECTIONS) {
    const box = el("section", "card");
    box.append(el("h2", "font-semibold", section.title));
    const grid = el("div", "mt-4 grid gap-4 sm:grid-cols-2");
    section.fields.forEach((f) => grid.append(buildField(f)));
    box.append(grid);
    container.append(box);
  }
}

/** Copy saved profile values into the inputs. */
function fillForm(profile) {
  ALL_FIELDS.forEach((f) => { form.elements[f.name].value = profile[f.name] ?? ""; });
  document.getElementById("setup-banner").classList.toggle("hidden", profile.is_complete);
  logoShow(profile.logo); // null when no logo has been uploaded
}

// A GSTIN contains the state code (first 2 digits) and the PAN (characters 3-12).
function setupGstinAutofill() {
  form.elements.gstin.addEventListener("blur", () => {
    const gstin = form.elements.gstin.value.trim().toUpperCase();
    if (gstin.length !== 15) return;
    const state = form.elements.state_code;
    if ([...state.options].some((o) => o.value === gstin.slice(0, 2))) state.value = gstin.slice(0, 2);
    if (!form.elements.pan.value.trim()) form.elements.pan.value = gstin.slice(2, 12);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearErrors(form);
  setBusy(saveButton, true, "Saving...");
  try {
    const payload = {};
    ALL_FIELDS.forEach((f) => { payload[f.name] = form.elements[f.name].value.trim(); });
    const profile = await apiRequest("/business/profile/", { method: "PUT", body: payload });
    fillForm(profile); // show what the server actually saved (e.g. upper-cased GSTIN)
    showFormMessage(form, "Profile saved.", "success");
  } catch (err) {
    showFieldErrors(form, err);
  } finally {
    setBusy(saveButton, false);
  }
});

(async () => {
  if (!requireLogin()) return;
  renderNav("profile");
  buildForm();
  setupGstinAutofill();
  try {
    const [states, profile] = await Promise.all([
      apiRequest("/meta/states/"),
      apiRequest("/business/profile/"),
    ]);
    states.forEach((s) => form.elements.state_code.append(new Option(`${s.code} - ${s.name}`, s.code)));
    fillForm(profile); // after the state options exist, so the saved state can be selected
  } catch (err) {
    showFormMessage(form, err.message);
  }
})();