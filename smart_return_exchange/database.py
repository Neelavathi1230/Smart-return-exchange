"""SQLite schema, connection helper and first-run seed data."""
import os
import sqlite3
from datetime import datetime, timedelta, date

from werkzeug.security import generate_password_hash

import logic

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    phone       TEXT,
    password    TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'admin')),
    address     TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    category            TEXT NOT NULL,
    description         TEXT,
    price               REAL NOT NULL CHECK (price >= 0),
    image               TEXT,
    return_window_days  INTEGER NOT NULL DEFAULT 30,
    exchange_available  INTEGER NOT NULL DEFAULT 1,
    stock               INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);

-- An order = one purchase of a product by a customer. It stores the
-- purchase date, which is what the return window is measured from.
CREATE TABLE IF NOT EXISTS orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_code     TEXT NOT NULL UNIQUE,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    product_id     INTEGER NOT NULL REFERENCES products(id),
    quantity       INTEGER NOT NULL DEFAULT 1,
    amount         REAL NOT NULL,
    purchase_date  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS return_requests (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    request_code          TEXT NOT NULL UNIQUE,
    user_id               INTEGER NOT NULL REFERENCES users(id),
    product_id            INTEGER NOT NULL REFERENCES products(id),
    order_id              INTEGER NOT NULL REFERENCES orders(id),
    return_type           TEXT,
    reason                TEXT NOT NULL,
    description           TEXT,
    product_condition     TEXT NOT NULL,
    pickup_date           TEXT,
    refund_method         TEXT,
    image                 TEXT,
    eligibility           TEXT,
    smart_assessment      TEXT,
    smart_notes           TEXT,
    priority              TEXT DEFAULT 'Low',
    status                TEXT NOT NULL DEFAULT 'REQUESTED',
    admin_remarks         TEXT,
    customer_reply        TEXT,
    pickup_scheduled_date TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exchange_requests (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_code          TEXT NOT NULL UNIQUE,
    user_id                INTEGER NOT NULL REFERENCES users(id),
    product_id             INTEGER NOT NULL REFERENCES products(id),
    order_id               INTEGER NOT NULL REFERENCES orders(id),
    reason                 TEXT NOT NULL,
    description            TEXT,
    product_condition      TEXT NOT NULL,
    replacement_product_id INTEGER NOT NULL REFERENCES products(id),
    variant                TEXT,
    image                  TEXT,
    eligibility            TEXT,
    smart_assessment       TEXT,
    smart_notes            TEXT,
    priority               TEXT DEFAULT 'Low',
    status                 TEXT NOT NULL DEFAULT 'REQUESTED',
    admin_remarks          TEXT,
    customer_reply         TEXT,
    pickup_scheduled_date  TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    message     TEXT NOT NULL,
    link        TEXT,
    is_read     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

-- 1 return request -> 1 refund
CREATE TABLE IF NOT EXISTS refunds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    refund_code  TEXT NOT NULL UNIQUE,
    request_id   INTEGER NOT NULL UNIQUE REFERENCES return_requests(id),
    amount       REAL NOT NULL,
    method       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Processing', 'Completed')),
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

-- 1 exchange request -> 1 exchange (shipment of the replacement)
CREATE TABLE IF NOT EXISTS exchanges (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_code          TEXT NOT NULL UNIQUE,
    request_id             INTEGER NOT NULL UNIQUE REFERENCES exchange_requests(id),
    original_product_id    INTEGER NOT NULL REFERENCES products(id),
    replacement_product_id INTEGER NOT NULL REFERENCES products(id),
    status                 TEXT NOT NULL DEFAULT 'Pending',
    shipping_status        TEXT NOT NULL DEFAULT 'Not Shipped',
    created_at             TEXT NOT NULL,
    completed_at           TEXT
);

-- One row for every status change (drives the tracking timeline)
CREATE TABLE IF NOT EXISTS status_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_type  TEXT NOT NULL CHECK (request_type IN ('return', 'exchange')),
    request_id    INTEGER NOT NULL,
    status        TEXT NOT NULL,
    note          TEXT,
    created_at    TEXT NOT NULL
);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_connection(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ------------------------------------------------------------------ seeding
SAMPLE_PRODUCTS = [
    # name, category, description, price, image, window, exchange, stock
    ("Wireless Headphones", "Electronics", "Over-ear Bluetooth headphones with active noise cancellation and 30-hour battery life.", 2499, "headphones.svg", 30, 1, 25),
    ("Smart Watch", "Electronics", "Fitness smart watch with heart-rate monitor, sleep tracking and AMOLED display.", 4999, "smartwatch.svg", 15, 1, 15),
    ("Bluetooth Speaker", "Electronics", "Portable waterproof speaker with deep bass and 12-hour playtime.", 1999, "speaker.svg", 30, 1, 40),
    ("Laptop Backpack", "Bags", "Water-resistant 30L backpack with padded laptop compartment and USB charging port.", 1299, "backpack.svg", 30, 1, 60),
    ("Smartphone", "Electronics", "6.5-inch display, 128 GB storage, 50 MP camera. Sealed devices only (7-day window).", 14999, "smartphone.svg", 7, 0, 10),
    ("Mechanical Keyboard", "Accessories", "Compact RGB mechanical keyboard with blue switches and detachable cable.", 1499, "keyboard.svg", 14, 1, 35),
    ("Running Shoes", "Footwear", "Lightweight breathable running shoes with cushioned sole. Sizes 6 to 11.", 2999, "shoes.svg", 30, 1, 50),
    ("Cotton Hoodie", "Clothing", "Soft fleece-lined cotton hoodie available in S, M, L and XL.", 999, "hoodie.svg", 15, 1, 100),
]


def init_db(app):
    cfg = app.config
    os.makedirs(os.path.dirname(cfg["DATABASE"]), exist_ok=True)
    conn = get_connection(cfg["DATABASE"])
    conn.executescript(SCHEMA)
    conn.commit()

    if not conn.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone():
        conn.execute(
            "INSERT INTO users (name, email, phone, password, role, address, created_at) "
            "VALUES (?, ?, ?, ?, 'admin', ?, ?)",
            (cfg["ADMIN_NAME"], cfg["ADMIN_EMAIL"], "9000000000",
             generate_password_hash(cfg["ADMIN_PASSWORD"]), "Head Office", now()))

    if not conn.execute("SELECT 1 FROM products").fetchone():
        for name, cat, desc, price, img, window, exch, stock in SAMPLE_PRODUCTS:
            conn.execute(
                "INSERT INTO products (name, category, description, price, image, "
                "return_window_days, exchange_available, stock, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (name, cat, desc, price, f"img/products/{img}", window, exch, stock, now()))
    conn.commit()

    if cfg["SEED_DEMO_DATA"] and not conn.execute(
            "SELECT 1 FROM users WHERE email = ?", (cfg["DEMO_CUSTOMER_EMAIL"],)).fetchone():
        seed_demo(conn, cfg)
        conn.commit()
    conn.close()


def _ts(days_ago, hour=10):
    d = datetime.now().replace(hour=hour, minute=15, second=0, microsecond=0)
    return (d - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def seed_demo(conn, cfg):
    """Demo customer with orders and a few requests in different statuses."""
    cur = conn.execute(
        "INSERT INTO users (name, email, phone, password, role, address, created_at) "
        "VALUES (?, ?, ?, ?, 'customer', ?, ?)",
        ("Demo Customer", cfg["DEMO_CUSTOMER_EMAIL"], "9876543210",
         generate_password_hash(cfg["DEMO_CUSTOMER_PASSWORD"]),
         "12 MG Road, Bengaluru, Karnataka", _ts(80)))
    uid = cur.lastrowid

    def product(name):
        return conn.execute("SELECT * FROM products WHERE name = ?", (name,)).fetchone()

    def order(name, days_ago, qty=1):
        p = product(name)
        code = logic.next_code(conn, "orders", "order_code", "ORD")
        cur = conn.execute(
            "INSERT INTO orders (order_code, user_id, product_id, quantity, amount, purchase_date, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (code, uid, p["id"], qty, p["price"] * qty,
             (date.today() - timedelta(days=days_ago)).isoformat(), _ts(days_ago)))
        return cur.lastrowid, p

    def history(rtype, rid, steps):
        for status, days_ago, note in steps:
            conn.execute(
                "INSERT INTO status_history (request_type, request_id, status, note, created_at) VALUES (?,?,?,?,?)",
                (rtype, rid, status, note, _ts(days_ago, 11)))

    def notify(message, days_ago, link, read=1):
        conn.execute(
            "INSERT INTO notifications (user_id, message, link, is_read, created_at) VALUES (?,?,?,?,?)",
            (uid, message, link, read, _ts(days_ago, 12)))

    def add_return(oid, p, order_days, created_days, reason, cond, desc, status, hist,
                   remarks=None, pickup=None):
        code = logic.next_code(conn, "return_requests", "request_code", "RET")
        label, notes = logic.smart_assess(reason, cond, order_days - created_days,
                                          p["return_window_days"], False, p["category"])
        cur = conn.execute(
            "INSERT INTO return_requests (request_code, user_id, product_id, order_id, return_type, reason, "
            "description, product_condition, pickup_date, refund_method, image, eligibility, smart_assessment, "
            "smart_notes, priority, status, admin_remarks, pickup_scheduled_date, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, uid, p["id"], oid, "Full Return", reason, desc, cond,
             (date.today() + timedelta(days=2)).isoformat(), "Original Payment Method", None,
             "Eligible", label, notes, logic.priority_for(reason), status, remarks, pickup,
             _ts(created_days), _ts(hist[-1][1])))
        rid = cur.lastrowid
        history("return", rid, hist)
        return rid, code

    # ---- orders
    o_speaker, p_speaker = order("Bluetooth Speaker", 50)
    o_phone, p_phone = order("Smartphone", 70)
    o_head, p_head = order("Wireless Headphones", 3)
    o_bag, p_bag = order("Laptop Backpack", 10)
    o_key, p_key = order("Mechanical Keyboard", 5)
    order("Cotton Hoodie", 2)      # free order - still eligible
    order("Smart Watch", 20)       # window (15 days) already expired
    order("Running Shoes", 40)     # window (30 days) already expired

    # ---- A: completed return with a completed refund
    rid, code = add_return(
        o_speaker, p_speaker, 50, 44, "Defective Product", "Opened but unused",
        "The speaker stops playing after a few minutes and the left channel is silent.",
        "COMPLETED",
        [("REQUESTED", 44, None), ("UNDER REVIEW", 43, None), ("APPROVED", 42, "Defect confirmed from description."),
         ("PICKUP SCHEDULED", 41, None), ("PICKED UP", 40, None), ("QUALITY CHECK", 38, None),
         ("REFUND INITIATED", 37, None), ("COMPLETED", 35, None)],
        remarks="Defect confirmed. Refund completed.", pickup=(date.today() - timedelta(days=41)).isoformat())
    conn.execute(
        "INSERT INTO refunds (refund_code, request_id, amount, method, status, created_at, completed_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (logic.next_code(conn, "refunds", "refund_code", "REF"), rid, p_speaker["price"],
         "Original Payment Method", "Completed", _ts(37), _ts(35)))
    notify(f"Your request {code} has been completed. Thank you!", 35, f"/requests/return/{rid}")

    # ---- B: rejected return
    rid, code = add_return(
        o_phone, p_phone, 70, 65, "Quality Issue", "Used",
        "I do not like the camera quality compared to my old phone.", "REJECTED",
        [("REQUESTED", 65, None), ("UNDER REVIEW", 64, None),
         ("REJECTED", 63, "No defect found. Change of mind is not covered for used phones.")],
        remarks="No defect found. Change of mind is not covered for used phones.")
    notify(f"Your request {code} was rejected. Reason: no defect found.", 63, f"/requests/return/{rid}")

    # ---- C: return under review
    rid, code = add_return(
        o_head, p_head, 3, 1, "Damaged Product", "Damaged",
        "The left ear cup was cracked when I opened the box.", "UNDER REVIEW",
        [("REQUESTED", 1, None), ("UNDER REVIEW", 0, None)])
    notify(f"Your return request {code} has been submitted.", 1, f"/requests/return/{rid}")
    notify(f"Your request {code} is now under review.", 0, f"/requests/return/{rid}", read=0)

    # ---- D: exchange being processed
    ecode = logic.next_code(conn, "exchange_requests", "exchange_code", "EXC")
    label, notes = logic.smart_assess("Size Issue", "Unopened", 4, p_bag["return_window_days"], False, p_bag["category"])
    cur = conn.execute(
        "INSERT INTO exchange_requests (exchange_code, user_id, product_id, order_id, reason, description, "
        "product_condition, replacement_product_id, variant, image, eligibility, smart_assessment, smart_notes, "
        "priority, status, admin_remarks, pickup_scheduled_date, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ecode, uid, p_bag["id"], o_bag, "Size Issue", "The 30L bag is too big for me. I would like the 25L one.",
         "Unopened", p_bag["id"], "Grey, 25L", None, "Eligible", label, notes, "Low",
         "EXCHANGE PROCESSING", "Replacement approved.", (date.today() - timedelta(days=4)).isoformat(),
         _ts(6), _ts(1)))
    eid = cur.lastrowid
    history("exchange", eid, [("REQUESTED", 6, None), ("UNDER REVIEW", 5, None), ("APPROVED", 5, None),
                              ("PICKUP SCHEDULED", 4, None), ("PICKED UP", 3, None), ("QUALITY CHECK", 2, None),
                              ("EXCHANGE PROCESSING", 1, "Replacement approved.")])
    conn.execute(
        "INSERT INTO exchanges (exchange_code, request_id, original_product_id, replacement_product_id, status, "
        "shipping_status, created_at) VALUES (?,?,?,?,?,?,?)",
        (logic.next_code(conn, "exchanges", "exchange_code", "EXCH"), eid, p_bag["id"], p_bag["id"],
         "Replacement Prepared", "Not Shipped", _ts(5)))
    notify(f"Exchange {ecode} is being processed. Your replacement is being prepared.", 1,
           f"/requests/exchange/{eid}", read=0)

    # ---- E: return with pickup scheduled
    rid, code = add_return(
        o_key, p_key, 5, 3, "Missing Parts", "Opened but unused",
        "The box did not contain the keycap puller and the USB cable.", "PICKUP SCHEDULED",
        [("REQUESTED", 3, None), ("UNDER REVIEW", 2, None), ("APPROVED", 2, None),
         ("PICKUP SCHEDULED", 1, "Pickup scheduled for tomorrow.")],
        remarks="Pickup scheduled.", pickup=(date.today() + timedelta(days=1)).isoformat())
    notify(f"Pickup scheduled for {(date.today() + timedelta(days=1)).strftime('%d %b %Y')} for request {code}.",
           1, f"/requests/return/{rid}", read=0)
