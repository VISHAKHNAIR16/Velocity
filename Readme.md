# Velocity -- GST Billing, Inventory & Accounting System

A cloud-native, multi-tenant GST invoicing, inventory management, and business accounting platform modeled after tools like Vyapar and GetSwipe. Designed for small-to-medium enterprises (SMEs), it automates tax calculations, tracks stock levels in real time, and simplifies client ledgers.

---

## 🚀 Key Features

### Invoicing & GST
* **GST Invoicing Engine:** Automated CGST, SGST, and IGST calculations based on intra-state and inter-state transactions with customizable PDF bill generation.
* **Quotations & Estimates:** Create quotes, convert to invoices with one click.
* **Sales Returns:** Credit notes against issued invoices.
* **Invoice PDF:** Server-side rendered, GST-compliant invoice PDFs with snapshot letterhead.

### Inventory & Purchases
* **Inventory Management:** Item and service catalogs, stock level tracking with auto-deductions on sales, purchase bill entries, stock movements ledger, and low-stock threshold alerts.
* **Purchase Bills:** Record vendor purchases, auto-replenish stock, input-tax credit tracking.

### Payments & Ledgers
* **Payments & Accounts:** Payments In / Payments Out, automated invoice status updates (UNPAID → PARTIAL → PAID), outstanding receivables/payables tracking.
* **Party Ledgers:** Customer and vendor profile management with GSTIN validation, billing/shipping address handling, running-balance statements with date-range filtering.
* **Expense Tracking:** Operational expense logging with categories, tax paid, and receipt URLs.

### GST Compliance
* **GSTR-1 & GSTR-3B Reports:** Exportable tax reports (CSV and Excel) formatted for GST Portal filing, with B2B / B2C Large / B2C Small / HSN summary sections.

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|------------|
| **Backend** | Python 3.11+, Django 5.x, Django REST Framework (DRF) |
| **Frontend** | HTML5, CSS3, Vanilla JavaScript (Tailwind CSS CDN), static SPA |
| **Database** | PostgreSQL (Hosted on Supabase / Neon) |
| **Backend Hosting** | Render |
| **Frontend Hosting** | Vercel |
| **Media & File Storage** | Cloudinary (Business logos & generated PDF invoices) |
| **Mobile (Future)** | Flutter (iOS/Android connecting to DRF APIs) |

---

## 📦 Project Structure

```
VELOCITY/
├── Backend/
│   ├── config/             # Django project settings, URLs, exception handling
│   ├── apps/
│   │   ├── accounts/       # Users, BusinessProfile, authentication
│   │   ├── core/           # TenantModel base, money helpers, GST constants, errors
│   │   ├── parties/        # Customers & vendors, party ledger engine
│   │   ├── inventory/      # Items, stock movements ledger (append-only)
│   │   ├── invoices/       # Invoices, invoice items, GST calculator, GSTR reports
│   │   ├── payments/       # Payments In/Out (append-only), invoice recalculation
│   │   ├── purchases/      # Purchase bills, purchase items, purchase returns
│   │   ├── expenses/       & Expense categories, operational expenses
│   │   ├── quotations/     & Quotes, convert-to-invoice
│   │   └── dashboard/      # Business analytics, low-stock alerts
│   ├── Invoice_Templates/  # PDF invoice HTML templates
│   ├── manage.py
│   ├── requirements.txt
│   └── pyproject.toml
├── Web_Frontend/
│   ├── auth/               # Login, register pages
│   ├── business/           # Business profile
│   ├── dashboard/          # Analytics dashboard
│   ├── parties/            # Party list, ledger statements
│   ├── items/              # Item catalogue, stock history
│   ├── invoices/           # Invoice create, preview, issue
│   ├── payments/           # Payment recording
│   ├── purchases/          # Purchase bills
│   ├── expenses/           # Expense logging
│   ├── quotations/         # Quote creation, convert to invoice
│   ├── gstr/               # GSTR-1 / GSTR-3B reports
│   ├── shared/             # Shared JS (api.js, ui.js, config.js) & CSS
│   └── tests/              # Frontend tests (Node + jsdom)
├── tasks.md                # Phased task tracker with decisions & gates
├── FUTURE_CHECKLIST.md     # Post-core improvements & production checklist
├── TECH.md                 # Deployment & infrastructure notes
├── Readme.md               # This file
└── docs/
    └── QA_CHECKLIST.md     # Manual browser QA checklist
```

---

## 🔌 API Endpoints

All endpoints are prefixed with `/api/v1/` and require JWT authentication unless noted.

| Resource | Endpoint | Methods |
|----------|----------|---------|
| Auth | `/auth/register/`, `/auth/login/`, `/auth/refresh/` | POST |
| Parties | `/parties/` | GET, POST, PUT, PATCH, DELETE |
| Items | `/items/` | GET, POST, PUT, PATCH, DELETE |
| Invoices | `/invoices/`, `/invoices/{id}/issue/`, `/invoices/{id}/cancel/`, `/invoices/{id}/pdf/`, `/invoices/preview/` | GET, POST, PUT, PATCH, DELETE |
| Payments | `/payments/` | GET, POST (append-only) |
| Purchases | `/purchases/`, `/purchases/returns/` | GET, POST, PUT, PATCH, DELETE |
| Expenses | `/expenses/`, `/expenses/summary/`, `/expense-categories/` | GET, POST, PUT, PATCH, DELETE |
| Quotations | `/quotations/`, `/quotations/{id}/convert-to-invoice/` | GET, POST, PUT, PATCH, DELETE |
| GST Reports | `/gstr1/?period=YYYY-MM`, `/gstr3b/?period=YYYY-MM` (+ `?format=csv\|xlsx`) | GET |
| Dashboard | `/dashboard/stats/`, `/dashboard/low-stock/` | GET |
| Health | `/api/v1/health/` | GET |

Error responses follow a consistent envelope:
```json
{
  "success": false,
  "error": "ERROR_CODE",
  "message": "Human-readable message",
  "errors": { "field": ["message"] }
}
```

---

## 🏗 Development

### Prerequisites
* Python 3.11+
* Node.js (for frontend tests)
* PostgreSQL (or SQLite for local development)

### Backend Setup
```bash
cd Backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # configure SECRET_KEY, DATABASE_URL, etc.
python manage.py migrate
python manage.py runserver
```

### Frontend
The frontend is static HTML/JS. Open any page in a browser or serve with a static server:
```bash
cd Web_Frontend
npx serve -l 3000
```

### Running Tests
```bash
# Backend
cd Backend
python manage.py test

# Frontend
cd Web_Frontend
node --test "tests/*.test.js"
```

### Linting
```bash
cd Backend
python -m ruff check apps/
python -m ruff format --check apps/
```

---

## 📊 Development Status

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 1 | Core foundation (auth, parties, items, invoices) | ✅ Complete |
| Phase 2 | PDF templates, stock ledger | ✅ Complete |
| Phase 3 | Payments, party statements, dashboard | ✅ Complete |
| Phase 4 | Purchases, expenses, quotations, GSTR reports | ✅ Complete |
| Phase 5 | Security hardening, production deployment | ⬜ Planned |
| Phase 6+ | E-invoicing, multi-business, Flutter app | ⬜ Vision |

See [`tasks.md`](tasks.md) for the full phased plan with decisions and gates.

---

## 📄 License

MIT License. See `LICENSE` for details.

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes (`git commit -m "feat: ..."`)
4. Push to the branch (`git push origin feature/your-feature`)
5. Open a Pull Request

---

## 📞 Support

For issues, feature requests, or questions, open a GitHub issue.
