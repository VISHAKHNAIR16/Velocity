# Velocity -- GST Billing, Inventory & Accounting System

A cloud-native, multi-tenant GST invoicing, inventory management, and business accounting platform modeled after tools like Vyapar and GetSwipe. Designed for small-to-medium enterprises (SMEs), it automates tax calculations, tracks stock levels in real time, and simplifies client ledgers.

---

## 🚀 Key Features

* **GST Invoicing Engine:** Automated CGST, SGST, and IGST calculations based on intra-state and inter-state transactions with customizable PDF bill generation.
* **Inventory Management:** Item and service catalogs, stock level tracking with auto-deductions on sales, purchase bill entries, and low-stock threshold alerts.
* **Parties & CRM:** Customer and vendor profile management with GSTIN validation, billing/shipping address handling, and real-time ledger statements.
* **Payments & Accounts:** Partial and full payment tracking (Payments In / Payments Out), automated invoice status updates, and outstanding receivables/payables tracking.
* **GST Compliance:** Exportable GSTR-1 and GSTR-3B tax reports formatted for simplified tax filing.

---

## 🛠 Tech Stack

* **Backend:** Python 3.11+, Django, Django REST Framework (DRF)
* **Frontend:** HTML5, CSS3, JavaScript (Static Web SPA / Django Views)
* **Database:** PostgreSQL (Hosted on Supabase)
* **Hosting:** Render (Backend Web Service), Vercel (Frontend Static Assets)
* **Media & File Storage:** Cloudinary (Business logos & generated PDF invoices)
* **Mobile (Future):** Flutter (iOS/Android connecting to DRF APIs)

