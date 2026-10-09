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
    { name: "state_code", label: "State", type: "select", placeholder: "Select state" },
    { name: "pincode", label: "Pincode", maxlength: 6 },
  ]},
  { title: "Bank details", fields: [
    { name: "bank_account_name", label: "Account holder name" },
    { name: "bank_account_number", label: "Account number", maxlength: 18 },
    { name: "bank_ifsc", label: "IFSC code", maxlength: 11, upper: true },
    { name: "bank_name", label: "Bank name" },
    { name: "bank_branch", label: "Branch" },
  ]},
  { title: "Invoice preferences", intro: "How your invoices are numbered and what they must contain.", fields: [
    {
      name: "gst_registration_type", label: "GST registration", type: "select",
      options: [
        ["REGULAR", "Regular - I am registered and charge GST"],
        ["UNREGISTERED", "Unregistered - I issue a Bill of Supply, no GST"],
        ["COMPOSITE", "Composition scheme"],
      ],
      hint: "An unregistered business issues a Bill of Supply and never charges GST.",
    },
    {
      name: "invoice_number_prefix", label: "Invoice number prefix", maxlength: 4, upper: true,
      placeholder: "INV",
      hint: "1-4 letters or digits. Locked in for the whole financial year once you issue your first invoice, so the series always looks consistent.",
    },
    {
name: "hsn_min_digits", label: "HSN / SAC digits required", type: "select",
      options: [
        ["4", "4 digits - annual turnover up to ?5 crore"],
        ["6", "6 digits - annual turnover above ?5 crore"],
      ],
      hint: "Every line of every tax invoice needs an HSN/SAC code, and your GSTR-1 asks for a number of digits based on your turnover. 4 digits up to ?5 crore, 6 above.",
      warning: "If you are unsure which applies, leave this at 4 and ask your accountant. A missing digit is harder to fix after filing than an extra one.",
    },
    {
      name: "round_invoice_total", label: "Round the invoice total", type: "checkbox",
      hint: "Adds a 'Round Off' row so the payable amount is a whole rupee.",
      warning: "Presentation only. This does not change taxable values, CGST/SGST/IGST, or your GSTR-1 figures - only the amount the customer pays.",
    },
  ]},
];
const ALL_FIELDS = SECTIONS.flatMap((s) => s.fields);
// Checkbox values are booleans, not strings: collect them from the DOM instead.
const TEXT_FIELDS = ALL_FIELDS.filter((f) => f.type !== "checkbox");

const form = document.getElementById("profile-form");
const saveButton = form.querySelector("button[type=submit]");

/** Create a small element with optional class and text. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

/** Build one label + input/select/checkbox + hint/warning + error line. */
function buildField(f) {
  const wrap = el("div");

  // Checkbox: label sits beside the box, and the hint/warning sit under both.
  if (f.type === "checkbox") {
    const row = el("div", "flex items-start gap-2.5");
    const input = el("input", "checkbox");
    input.type = "checkbox";
    input.id = f.name;
    input.name = f.name;
    const label = el("label", "text-sm font-medium text-navy");
    label.htmlFor = f.name;
    label.textContent = f.label;
    row.append(input, label);
    wrap.append(row);
    wrap.append(f.hint ? el("p", "hint", f.hint) : document.createComment(""));
    if (f.warning) wrap.append(buildWarning(f.warning));
    const boxError = el("p", "field-error");
    boxError.dataset.errorFor = f.name;
    wrap.append(boxError);
    return wrap;
  }

  const label = el("label", "label", f.label + (f.required ? " *" : ""));
  label.htmlFor = f.name;

  const input = f.type === "select" ? el("select", "input") : el("input", "input");
  input.id = f.name;
  input.name = f.name;
  if (f.type === "select") {
    input.append(new Option(f.placeholder || "Select", ""));
    (f.options || []).forEach(([value, text]) => input.append(new Option(text, value)));
  } else {
    input.type = f.type || "text";
    if (f.maxlength) input.maxLength = f.maxlength;
    if (f.placeholder) input.placeholder = f.placeholder;
    if (f.upper) input.classList.add("uppercase"); // display only; the backend stores upper case
  }

  wrap.append(label, input);
  if (f.hint) wrap.append(el("p", "hint", f.hint));
  if (f.warning) wrap.append(buildWarning(f.warning));
  const error = el("p", "field-error");
  error.dataset.errorFor = f.name;
  wrap.append(error);
  return wrap;
}

/** A caution note under a setting whose consequences are easy to underestimate. */
function buildWarning(text) {
  const box = el("p", "mt-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800");
  box.textContent = text;
  return box;
}

/** Draw all sections into the page. */
function buildForm() {
  const container = document.getElementById("sections");
  for (const section of SECTIONS) {
    const box = el("section", "card");
    box.append(el("h2", "font-semibold", section.title));
    if (section.intro) box.append(el("p", "hint mt-1", section.intro));
    const grid = el("div", "mt-4 grid gap-4 sm:grid-cols-2");
    section.fields.forEach((f) => grid.append(buildField(f)));
    box.append(grid);
    container.append(box);
  }
}

/** Copy saved profile values into the inputs. */
function fillForm(profile) {
  TEXT_FIELDS.forEach((f) => { form.elements[f.name].value = profile[f.name] ?? ""; });
  ALL_FIELDS.filter((f) => f.type === "checkbox").forEach((f) => {
    form.elements[f.name].checked = Boolean(profile[f.name]);
  });
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
    TEXT_FIELDS.forEach((f) => { payload[f.name] = form.elements[f.name].value.trim(); });
    ALL_FIELDS.filter((f) => f.type === "checkbox").forEach((f) => {
      payload[f.name] = form.elements[f.name].checked;
    });
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