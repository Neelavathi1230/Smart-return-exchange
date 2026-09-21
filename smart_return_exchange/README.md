# Smart Return & Exchange Management Portal

A complete Web Technology project: customers request **returns** and **exchanges**, admins review them, and everyone can track the full lifecycle.

**Stack:** HTML5 · CSS3 · JavaScript · Bootstrap 5 · Python Flask · SQLite · Jinja2 · Flask sessions · Werkzeug password hashing · Font Awesome (+ Chart.js for the admin charts). No Node, React, MongoDB or Docker needed.

---

## 1. How to run (Windows + VS Code)

1. Install **Python 3.9+** from python.org (tick *"Add Python to PATH"*).
2. Unzip the project and open the folder `smart_return_exchange` in VS Code.
3. Open the terminal in VS Code (**Terminal → New Terminal**) and run:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

4. Open **http://127.0.0.1:5000** in your browser.

The database (`instance/database.db`) and sample data are created **automatically** on the first run. To reset everything, stop the app and delete `instance/database.db`.

> You need an internet connection when viewing the pages: Bootstrap, Font Awesome and Chart.js are loaded from CDNs.

## 2. Demo accounts

| Role | Email | Password |
|------|-------|----------|
| Admin | `admin@smartreturn.com` | `admin123` |
| Customer (with sample data) | `demo@customer.com` | `customer123` |

> ⚠️ **The admin account above is only a demo account. Change the password (Profile → Change password) and set a strong `SECRET_KEY` before any real deployment.** Set `SEED_DEMO_DATA = False` in `config.py` to start without demo data.

You can also register new customers from the Register page.

## 3. Quick tour

**Customer:** Register → Products → *Buy (demo)* → My Orders → *Request Return / Exchange* → track on **My Requests** → notifications → refund details.
There is no real checkout, so a *demo purchase* creates an order. Use **"Purchased days ago"** to test the return window (e.g. 40 days ago = expired).

**Admin:** Dashboard (stats + charts) → Requests (filter/search) → open a request → Start review → Approve / Reject / Request info → Schedule pickup → Picked up → Quality check → Initiate refund *or* Process exchange → Mark completed. Also manage products, customers and notifications.

## 4. Request lifecycle

```
REQUESTED → UNDER REVIEW → APPROVED → PICKUP SCHEDULED → PICKED UP → QUALITY CHECK
          → REFUND INITIATED (returns) / EXCHANGE PROCESSING (exchanges) → COMPLETED
Rejected at review or quality check → REJECTED     |  Admin asks a question → INFO REQUESTED
```
Refund status: Pending → Processing → Completed.
Exchange status: Pending → Approved → Replacement Prepared → Shipped → Delivered → Completed.
Every change is stored in `status_history`, and the customer gets a notification.

## 5. Smart Return Assessment (rule-based)

`logic.smart_assess()` gives a score from the **reason, product condition, days since purchase, evidence image and category** and labels the request *Likely Eligible*, *Needs Manual Review* or *Likely Not Eligible*. It uses plain `if` rules — no AI or external API — and is shown with the note: *"This assessment is only a decision-support feature. Final approval is performed by the administrator."*

## 6. Folder structure

```
smart_return_exchange/
├── app.py            # Flask routes (customer + admin)
├── config.py         # settings, demo accounts
├── database.py       # SQLite schema + first-run seed data
├── logic.py          # eligibility, smart assessment, codes, timeline
├── requirements.txt
├── instance/database.db      # created automatically
├── uploads/evidence/         # customer evidence images (private)
├── static/{css,js,img,uploads/products}
└── templates/                # Jinja2 pages
```

## 7. Database tables

`users`, `products`, `orders`, `return_requests`, `exchange_requests`, `notifications`, `refunds`, `exchanges`, `status_history`.

```
users 1──* orders *──1 products
users 1──* return_requests  1──1 refunds
users 1──* exchange_requests 1──1 exchanges
return_requests / exchange_requests ──> products, orders
users 1──* notifications
```
Small differences from a minimal design (on purpose): an **orders** table holds the *purchase date* that the return window is measured from; `condition` is stored as `product_condition`; each request has a `priority` (High/Medium/Low from the reason).

## 8. Security features

- Passwords hashed with Werkzeug (`generate_password_hash`)
- Login sessions, **role checks** on every customer/admin route (`login_required`)
- **CSRF token** on every POST form
- All SQL uses **parameterised queries** (no SQL injection); Jinja2 auto-escapes output (no XSS)
- Uploads: extension whitelist, real image signature check, `secure_filename`, random name, 5 MB limit
- Evidence images live **outside** `static/` and only the owner/admin can open them
- Customers can only open their own requests; the public *Track Request* page shows status only (no personal data)

## 9. Rules implemented

- Return window per product; expired orders cannot be returned/exchanged
- One open request per order (a rejected one can be re-submitted)
- Exchange needs `exchange_available` and an in-stock replacement; stock is reserved when the exchange is processed
- Products used in orders/requests cannot be deleted (set stock to 0 instead)

## 10. Troubleshooting

- *`python` not found* → reinstall Python with "Add to PATH", or use `py app.py`.
- *Port in use* → change the port: `app.run(debug=True, port=5001)` at the bottom of `app.py`.
- *Styles missing* → check your internet connection (CDN files).
