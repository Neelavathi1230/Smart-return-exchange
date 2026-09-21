"""Smart Return & Exchange Management Portal - main Flask application.

Run with:  python app.py   ->  http://127.0.0.1:5000
"""
import os
import re
import secrets
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from config import Config
from database import get_connection, init_db, now
import logic
from logic import (ACTIONS, EXCHANGE_STATUSES, PRODUCT_CATEGORIES,
                   PRODUCT_CONDITIONS, REFUND_METHODS, REFUND_STATUSES,
                   RETURN_REASONS, RETURN_TYPES, STATUS_GROUPS, STATUSES,
                   available_actions, build_timeline, check_eligibility,
                   next_code, priority_for, smart_assess)

app = Flask(__name__)
app.config.from_object(Config)

for folder in (app.config["PRODUCT_UPLOAD_FOLDER"], app.config["EVIDENCE_UPLOAD_FOLDER"]):
    os.makedirs(folder, exist_ok=True)
init_db(app)  # creates tables, demo admin, sample products (first run only)

TABLES = {"return": "return_requests", "exchange": "exchange_requests"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d{10,13}$")

# Both request tables in one "virtual table" (used by lists and dashboard)
UNION_SQL = """
    SELECT 'return' AS rtype, r.id, r.request_code AS code, r.user_id, r.product_id,
           r.order_id, r.reason, r.status, r.priority, r.created_at
      FROM return_requests r
    UNION ALL
    SELECT 'exchange', e.id, e.exchange_code, e.user_id, e.product_id,
           e.order_id, e.reason, e.status, e.priority, e.created_at
      FROM exchange_requests e
"""


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = get_connection(app.config["DATABASE"])
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ----------------------------------------------------------- auth + CSRF
def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    if "user" not in g:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return g.user


def login_required(role=None):
    """Protect a route. Use @login_required() or @login_required('admin')."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                session.clear()
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if role and user["role"] != role:
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = session.get("_csrf")
        if not token or token != request.form.get("csrf_token"):
            abort(400)


# ------------------------------------------------------- template helpers
@app.context_processor
def inject_globals():
    user = current_user()
    unread = 0
    if user:
        unread = get_db().execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
            (user["id"],)).fetchone()[0]
    return {
        "current_user": user, "unread_count": unread,
        "currency": app.config["CURRENCY"],
        "layout": "admin_base.html" if user and user["role"] == "admin" else "base.html",
        "today": date.today().isoformat(),
    }


def _parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value)[:19], fmt)
        except ValueError:
            continue
    return None


@app.template_filter("dt")
def format_datetime(value):
    d = _parse_dt(value)
    return d.strftime("%d %b %Y, %I:%M %p") if d else "-"


@app.template_filter("d")
def format_date(value):
    d = _parse_dt(value)
    return d.strftime("%d %b %Y") if d else "-"


@app.template_filter("money")
def format_money(value):
    try:
        return f"{app.config['CURRENCY']}{float(value):,.2f}"
    except (TypeError, ValueError):
        return "-"


@app.template_filter("badge")
def status_badge_class(status):
    return "status-" + str(status).lower().replace(" ", "-")


def money(value):
    return f"{app.config['CURRENCY']}{value:,.2f}"


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def valid_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ file upload
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]


def looks_like_image(head):
    """Check the first bytes of the file (a renamed .exe is not an image)."""
    return (head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff")
            or head.startswith((b"GIF87a", b"GIF89a"))
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


def save_image(file, folder):
    """Validate + save an uploaded image. Returns (filename, error_message)."""
    if not file or not file.filename:
        return None, None
    if not allowed_file(file.filename):
        return None, "Only PNG, JPG, JPEG, GIF or WEBP images are allowed."
    head = file.stream.read(16)
    file.stream.seek(0)
    if not looks_like_image(head):
        return None, "The uploaded file is not a valid image."
    ext = file.filename.rsplit(".", 1)[1].lower()
    safe = secure_filename(file.filename) or f"upload.{ext}"
    name = f"{uuid.uuid4().hex[:12]}_{safe}"
    file.save(os.path.join(folder, name))
    return name, None


# ----------------------------------------------------- notifications etc.
def notify(db, user_id, message, link=None):
    db.execute("INSERT INTO notifications (user_id, message, link, is_read, created_at) VALUES (?,?,?,0,?)",
               (user_id, message, link, now()))


def notify_admins(db, message, link=None):
    for admin in db.execute("SELECT id FROM users WHERE role = 'admin'").fetchall():
        notify(db, admin["id"], message, link)


def add_history(db, rtype, rid, status, note=None):
    db.execute("INSERT INTO status_history (request_type, request_id, status, note, created_at) VALUES (?,?,?,?,?)",
               (rtype, rid, status, note, now()))


def set_status(db, rtype, rid, status, note=None, remarks=None):
    table = TABLES[rtype]
    if remarks:
        db.execute(f"UPDATE {table} SET status = ?, admin_remarks = ?, updated_at = ? WHERE id = ?",
                   (status, remarks, now(), rid))
    else:
        db.execute(f"UPDATE {table} SET status = ?, updated_at = ? WHERE id = ?", (status, now(), rid))
    add_history(db, rtype, rid, status, note or remarks)


def check_rtype(rtype):
    if rtype not in TABLES:
        abort(404)


def fetch_request(db, rtype, rid):
    """One request (return or exchange) joined with customer, product and order."""
    extra = """, rp.name AS replacement_name, rp.price AS replacement_price,
                   rp.image AS replacement_image, rp.stock AS replacement_stock"""
    if rtype == "return":
        sql = f"""SELECT r.*, 'return' AS rtype, r.request_code AS code,
                   u.name AS customer_name, u.email AS customer_email,
                   u.phone AS customer_phone, u.address AS customer_address,
                   p.name AS product_name, p.category, p.image AS product_image,
                   p.price AS product_price, p.return_window_days,
                   o.order_code, o.purchase_date, o.amount AS order_amount, o.quantity
                 FROM return_requests r
                 JOIN users u ON u.id = r.user_id
                 JOIN products p ON p.id = r.product_id
                 JOIN orders o ON o.id = r.order_id
                WHERE r.id = ?"""
    else:
        sql = f"""SELECT r.*, 'exchange' AS rtype, r.exchange_code AS code,
                   u.name AS customer_name, u.email AS customer_email,
                   u.phone AS customer_phone, u.address AS customer_address,
                   p.name AS product_name, p.category, p.image AS product_image,
                   p.price AS product_price, p.return_window_days,
                   o.order_code, o.purchase_date, o.amount AS order_amount, o.quantity
                   {extra}
                 FROM exchange_requests r
                 JOIN users u ON u.id = r.user_id
                 JOIN products p ON p.id = r.product_id
                 JOIN orders o ON o.id = r.order_id
                 JOIN products rp ON rp.id = r.replacement_product_id
                WHERE r.id = ?"""
    row = db.execute(sql, (rid,)).fetchone()
    return dict(row) if row else None


def request_extras(db, req):
    """History, timeline, and refund/exchange record for a request."""
    history = [dict(h) for h in db.execute(
        "SELECT * FROM status_history WHERE request_type = ? AND request_id = ? ORDER BY id",
        (req["rtype"], req["id"])).fetchall()]
    timeline = build_timeline(req["rtype"], req["status"], history)
    refund = exchange = None
    if req["rtype"] == "return":
        row = db.execute("SELECT * FROM refunds WHERE request_id = ?", (req["id"],)).fetchone()
        refund = dict(row) if row else None
    else:
        row = db.execute("SELECT * FROM exchanges WHERE request_id = ?", (req["id"],)).fetchone()
        exchange = dict(row) if row else None
    return history, timeline, refund, exchange


def active_request(db, order_id):
    """An order can only have one open (non-rejected) return/exchange request."""
    row = db.execute(
        """SELECT 'return' AS rtype, id, request_code AS code FROM return_requests
            WHERE order_id = ? AND status != 'REJECTED'
           UNION ALL
           SELECT 'exchange', id, exchange_code FROM exchange_requests
            WHERE order_id = ? AND status != 'REJECTED' LIMIT 1""",
        (order_id, order_id)).fetchone()
    return dict(row) if row else None


ORDER_SQL = """SELECT o.*, p.name AS product_name, p.category, p.image AS product_image,
                      p.return_window_days, p.exchange_available, p.price AS product_price
                 FROM orders o JOIN products p ON p.id = o.product_id"""


def get_order_for_user(db, order_id, user_id):
    row = db.execute(ORDER_SQL + " WHERE o.id = ? AND o.user_id = ?", (order_id, user_id)).fetchone()
    return dict(row) if row else None


def orders_with_state(db, user_id, product_id=None):
    sql = ORDER_SQL + " WHERE o.user_id = ?"
    params = [user_id]
    if product_id:
        sql += " AND o.product_id = ?"
        params.append(product_id)
    orders = []
    for row in db.execute(sql + " ORDER BY o.id DESC", params).fetchall():
        o = dict(row)
        o.update(check_eligibility(o["purchase_date"], o["return_window_days"]))
        o["active_request"] = active_request(db, o["id"])
        orders.append(o)
    return orders


def list_requests(db, where="", params=(), limit=None):
    sql = f"""SELECT q.*, u.name AS customer_name, u.email AS customer_email, p.name AS product_name
                FROM ({UNION_SQL}) q
                JOIN users u ON u.id = q.user_id
                JOIN products p ON p.id = q.product_id
               {where} ORDER BY q.created_at DESC, q.id DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def status_placeholders(statuses):
    return ",".join("?" for _ in statuses)


# ======================================================================
# PUBLIC PAGES
# ======================================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("dashboard"))
    form = {}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in ("name", "email", "phone", "address")}
        form["email"] = form["email"].lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        errors = []
        if len(form["name"]) < 2:
            errors.append("Full name is required.")
        if not EMAIL_RE.match(form["email"]):
            errors.append("Please enter a valid email address.")
        if not PHONE_RE.match(re.sub(r"[\s-]", "", form["phone"])):
            errors.append("Enter a valid phone number (10 to 13 digits).")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("Password and confirm password do not match.")
        if len(form["address"]) < 5:
            errors.append("Please enter your address.")
        db = get_db()
        if not errors and db.execute("SELECT 1 FROM users WHERE email = ?", (form["email"],)).fetchone():
            errors.append("An account with this email already exists.")
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            try:
                db.execute(
                    "INSERT INTO users (name, email, phone, password, role, address, created_at) "
                    "VALUES (?,?,?,?, 'customer', ?, ?)",
                    (form["name"], form["email"], re.sub(r"[\s-]", "", form["phone"]),
                     generate_password_hash(password), form["address"], now()))
                db.commit()
            except sqlite3.IntegrityError:
                flash("An account with this email already exists.", "danger")
                return render_template("register.html", form=form)
            flash("Registration successful! Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html", form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password"], password):
            session.clear()  # start a fresh session (prevents session fixation)
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            flash(f"Welcome back, {user['name']}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/track")
def track():
    """Public tracking by request ID. Shows only status - no personal data."""
    code = request.args.get("code", "").strip().upper()
    result = timeline = None
    can_open = False
    if code:
        db = get_db()
        rtype = "return" if code.startswith("RET-") else "exchange" if code.startswith("EXC-") else None
        row = None
        if rtype == "return":
            row = db.execute("""SELECT r.id, r.request_code AS code, r.status, r.created_at, r.user_id, p.name AS product_name
                                  FROM return_requests r JOIN products p ON p.id = r.product_id
                                 WHERE r.request_code = ?""", (code,)).fetchone()
        elif rtype == "exchange":
            row = db.execute("""SELECT r.id, r.exchange_code AS code, r.status, r.created_at, r.user_id, p.name AS product_name
                                  FROM exchange_requests r JOIN products p ON p.id = r.product_id
                                 WHERE r.exchange_code = ?""", (code,)).fetchone()
        if row:
            result = dict(row)
            result["rtype"] = rtype
            history = [dict(h) for h in db.execute(
                "SELECT * FROM status_history WHERE request_type = ? AND request_id = ? ORDER BY id",
                (rtype, row["id"])).fetchall()]
            timeline = build_timeline(rtype, row["status"], history)
            user = current_user()
            can_open = bool(user and (user["role"] == "admin" or user["id"] == row["user_id"]))
        else:
            flash(f"No request found with ID {code}.", "warning")
    return render_template("track.html", code=code, result=result, timeline=timeline, can_open=can_open)


@app.route("/evidence/<path:filename>")
@login_required()
def evidence(filename):
    """Evidence images are private: only the owner and admins can open them."""
    user = current_user()
    if user["role"] != "admin":
        owns = get_db().execute(
            "SELECT 1 FROM return_requests WHERE image = ? AND user_id = ? "
            "UNION SELECT 1 FROM exchange_requests WHERE image = ? AND user_id = ?",
            (filename, user["id"], filename, user["id"])).fetchone()
        if not owns:
            abort(404)
    return send_from_directory(app.config["EVIDENCE_UPLOAD_FOLDER"], filename)


# ======================================================================
# SHARED (customer + admin): profile, notifications
# ======================================================================
@app.route("/dashboard")
@login_required()
def dashboard():
    user = current_user()
    if user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    db = get_db()
    mine = list_requests(db, "WHERE q.user_id = ?", (user["id"],))
    stats = {
        "total": len(mine),
        "pending": sum(1 for r in mine if r["status"] in STATUS_GROUPS["pending"]),
        "in_progress": sum(1 for r in mine if r["status"] in STATUS_GROUPS["approved"]),
        "completed": sum(1 for r in mine if r["status"] == "COMPLETED"),
        "rejected": sum(1 for r in mine if r["status"] == "REJECTED"),
    }
    orders = orders_with_state(db, user["id"])[:4]
    return render_template("customer_dashboard.html", stats=stats, recent=mine[:5], orders=orders)


@app.route("/profile", methods=["GET", "POST"])
@login_required()
def profile():
    db = get_db()
    user = current_user()
    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "details":
            name = request.form.get("name", "").strip()
            phone = re.sub(r"[\s-]", "", request.form.get("phone", ""))
            address = request.form.get("address", "").strip()
            if len(name) < 2:
                flash("Name is required.", "danger")
            elif not PHONE_RE.match(phone):
                flash("Enter a valid phone number (10 to 13 digits).", "danger")
            elif len(address) < 5:
                flash("Please enter your address.", "danger")
            else:
                db.execute("UPDATE users SET name = ?, phone = ?, address = ? WHERE id = ?",
                           (name, phone, address, user["id"]))
                db.commit()
                flash("Profile updated.", "success")
        elif form_type == "password":
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            if not check_password_hash(user["password"], current):
                flash("Current password is incorrect.", "danger")
            elif len(new) < 6:
                flash("New password must be at least 6 characters.", "danger")
            elif new != confirm:
                flash("New password and confirmation do not match.", "danger")
            else:
                db.execute("UPDATE users SET password = ? WHERE id = ?",
                           (generate_password_hash(new), user["id"]))
                db.commit()
                flash("Password changed successfully.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")


@app.route("/notifications")
@login_required("customer")
def notifications():
    rows = get_db().execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC",
                            (current_user()["id"],)).fetchall()
    return render_template("notifications.html", items=[dict(r) for r in rows])


@app.route("/notifications/<int:nid>/go")
@login_required()
def notification_go(nid):
    db = get_db()
    row = db.execute("SELECT * FROM notifications WHERE id = ? AND user_id = ?",
                     (nid, current_user()["id"])).fetchone()
    if not row:
        abort(404)
    db.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (nid,))
    db.commit()
    target = row["link"] or ""
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("admin_notifications" if current_user()["role"] == "admin" else "notifications")
    return redirect(target)


@app.route("/notifications/read-all", methods=["POST"])
@login_required()
def notifications_read_all():
    db = get_db()
    db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (current_user()["id"],))
    db.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("admin_notifications" if current_user()["role"] == "admin" else "notifications"))


@app.route("/notifications/<int:nid>/read", methods=["POST"])
@login_required()
def notification_read(nid):
    db = get_db()
    db.execute("UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
               (nid, current_user()["id"]))
    db.commit()
    return redirect(url_for("admin_notifications" if current_user()["role"] == "admin" else "notifications"))


# ======================================================================
# CUSTOMER: products, orders, requests
# ======================================================================
@app.route("/products")
@login_required("customer")
def products():
    db = get_db()
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    sql, params = "SELECT * FROM products WHERE 1=1", []
    if q:
        sql += " AND (name LIKE ? OR description LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if category:
        sql += " AND category = ?"
        params.append(category)
    items = [dict(r) for r in db.execute(sql + " ORDER BY name", params).fetchall()]
    categories = [r[0] for r in db.execute("SELECT DISTINCT category FROM products ORDER BY category")]
    return render_template("products.html", items=items, categories=categories, q=q, category=category)


@app.route("/products/<int:pid>")
@login_required("customer")
def product_details(pid):
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not product:
        abort(404)
    orders = orders_with_state(db, current_user()["id"], pid)
    return render_template("product_details.html", product=dict(product), orders=orders)


@app.route("/products/<int:pid>/buy", methods=["POST"])
@login_required("customer")
def buy_product(pid):
    """DEMO purchase - creates an order so the customer has something to return.
    'Purchased days ago' lets you test the return window without waiting."""
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not product:
        abort(404)
    qty = to_int(request.form.get("quantity"), 1)
    days_ago = to_int(request.form.get("days_ago"), 0)
    if qty is None or not 1 <= qty <= 5:
        flash("Quantity must be between 1 and 5.", "danger")
    elif days_ago is None or not 0 <= days_ago <= 365:
        flash("'Purchased days ago' must be between 0 and 365.", "danger")
    elif product["stock"] < qty:
        flash("Sorry, not enough stock available.", "danger")
    else:
        code = next_code(db, "orders", "order_code", "ORD")
        db.execute(
            "INSERT INTO orders (order_code, user_id, product_id, quantity, amount, purchase_date, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (code, current_user()["id"], pid, qty, product["price"] * qty,
             (date.today() - timedelta(days=days_ago)).isoformat(), now()))
        db.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, pid))
        db.commit()
        flash(f"Demo order {code} placed. You can now request a return or exchange.", "success")
        return redirect(url_for("my_orders"))
    return redirect(url_for("product_details", pid=pid))


@app.route("/orders")
@login_required("customer")
def my_orders():
    return render_template("my_orders.html", orders=orders_with_state(get_db(), current_user()["id"]))


def handle_request_form(rtype):
    """Shared logic for 'Request Return' and 'Request Exchange' forms."""
    db = get_db()
    user = current_user()
    orders = orders_with_state(db, user["id"])
    replacements = [dict(r) for r in db.execute(
        "SELECT id, name, price, stock FROM products WHERE stock > 0 ORDER BY name").fetchall()]
    selected_order = request.args.get("order", type=int)
    form = {}

    if request.method == "POST":
        form = request.form.to_dict()
        selected_order = to_int(form.get("order_id"))
        errors = []
        order = get_order_for_user(db, selected_order, user["id"])
        word = "Return" if rtype == "return" else "Exchange"
        elig = None
        if not order:
            errors.append("Please select a valid product / order.")
        else:
            elig = check_eligibility(order["purchase_date"], order["return_window_days"])
            if not elig["eligible"]:
                errors.append(f"{word} window expired. This product could only be "
                              f"{rtype}ed within {order['return_window_days']} days of purchase.")
            if active_request(db, order["id"]):
                errors.append("A return/exchange request already exists for this order.")
            if rtype == "exchange" and not order["exchange_available"]:
                errors.append("Exchange is not available for this product.")

        reason = form.get("reason", "")
        condition = form.get("product_condition", "")
        description = form.get("description", "").strip()
        if reason not in RETURN_REASONS:
            errors.append("Please select a valid reason.")
        if condition not in PRODUCT_CONDITIONS:
            errors.append("Please select the product condition.")
        if len(description) < 10:
            errors.append("Please describe the problem (at least 10 characters).")

        pickup = refund_method = return_type = replacement_id = None
        variant = form.get("variant", "").strip()[:100]
        if rtype == "return":
            return_type = form.get("return_type", "")
            refund_method = form.get("refund_method", "")
            pickup = form.get("pickup_date", "")
            if return_type not in RETURN_TYPES:
                errors.append("Please select a return type.")
            if refund_method not in REFUND_METHODS:
                errors.append("Please select a refund method.")
            pd = valid_date(pickup)
            if not pd or pd < date.today():
                errors.append("Preferred pickup date must be today or a future date.")
        else:
            replacement_id = to_int(form.get("replacement_product_id"))
            repl = db.execute("SELECT * FROM products WHERE id = ?", (replacement_id,)).fetchone()
            if not repl or repl["stock"] < 1:
                errors.append("Please choose an in-stock replacement product.")
            elif order and repl["id"] == order["product_id"] and not variant:
                errors.append("Please mention the size/color you want for the same product.")

        image_name = None
        if not errors:
            image_name, img_error = save_image(request.files.get("image"), app.config["EVIDENCE_UPLOAD_FOLDER"])
            if img_error:
                errors.append(img_error)

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            label, notes = smart_assess(reason, condition, elig["days_since"], order["return_window_days"],
                                        bool(image_name), order["category"])
            eligibility = "Eligible"
            if rtype == "return":
                code = next_code(db, "return_requests", "request_code", "RET")
                cur = db.execute(
                    "INSERT INTO return_requests (request_code, user_id, product_id, order_id, return_type, reason, "
                    "description, product_condition, pickup_date, refund_method, image, eligibility, smart_assessment, "
                    "smart_notes, priority, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'REQUESTED', ?, ?)",
                    (code, user["id"], order["product_id"], order["id"], return_type, reason, description,
                     condition, pickup, refund_method, image_name, eligibility, label, notes,
                     priority_for(reason), now(), now()))
            else:
                code = next_code(db, "exchange_requests", "exchange_code", "EXC")
                cur = db.execute(
                    "INSERT INTO exchange_requests (exchange_code, user_id, product_id, order_id, reason, description, "
                    "product_condition, replacement_product_id, variant, image, eligibility, smart_assessment, "
                    "smart_notes, priority, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'REQUESTED', ?, ?)",
                    (code, user["id"], order["product_id"], order["id"], reason, description, condition,
                     replacement_id, variant, image_name, eligibility, label, notes,
                     priority_for(reason), now(), now()))
            rid = cur.lastrowid
            add_history(db, rtype, rid, "REQUESTED", "Request submitted by customer")
            notify(db, user["id"], f"Your {rtype} request {code} has been submitted.",
                   url_for("request_details", rtype=rtype, rid=rid))
            notify_admins(db, f"New {rtype} request {code} from {user['name']} ({order['product_name']}).",
                          url_for("admin_request_details", rtype=rtype, rid=rid))
            db.commit()
            flash(f"Your {rtype} request {code} was submitted successfully.", "success")
            return redirect(url_for("request_details", rtype=rtype, rid=rid))

    template = "create_return.html" if rtype == "return" else "create_exchange.html"
    return render_template(template, orders=orders, form=form, selected_order=selected_order,
                           reasons=RETURN_REASONS, conditions=PRODUCT_CONDITIONS,
                           refund_methods=REFUND_METHODS, return_types=RETURN_TYPES,
                           replacements=replacements)


@app.route("/returns/new", methods=["GET", "POST"])
@login_required("customer")
def create_return():
    return handle_request_form("return")


@app.route("/exchanges/new", methods=["GET", "POST"])
@login_required("customer")
def create_exchange():
    return handle_request_form("exchange")


@app.route("/requests")
@login_required("customer")
def my_requests():
    db = get_db()
    rtype = request.args.get("type", "")
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    where, params = ["q.user_id = ?"], [current_user()["id"]]
    if rtype in TABLES:
        where.append("q.rtype = ?")
        params.append(rtype)
    if status in STATUSES:
        where.append("q.status = ?")
        params.append(status)
    if q:
        where.append("(q.code LIKE ? OR p.name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    items = list_requests(db, "WHERE " + " AND ".join(where), params)
    return render_template("my_requests.html", items=items, rtype=rtype, status=status, q=q, statuses=STATUSES)


@app.route("/requests/<rtype>/<int:rid>")
@login_required("customer")
def request_details(rtype, rid):
    check_rtype(rtype)
    db = get_db()
    req = fetch_request(db, rtype, rid)
    if not req or req["user_id"] != current_user()["id"]:
        abort(404)
    history, timeline, refund, exchange = request_extras(db, req)
    return render_template("request_details.html", req=req, timeline=timeline, history=history,
                           refund=refund, exchange=exchange)


@app.route("/requests/<rtype>/<int:rid>/reply", methods=["POST"])
@login_required("customer")
def request_reply(rtype, rid):
    """Customer answers an admin's 'more information' request."""
    check_rtype(rtype)
    db = get_db()
    req = fetch_request(db, rtype, rid)
    if not req or req["user_id"] != current_user()["id"]:
        abort(404)
    message = request.form.get("reply", "").strip()
    if req["status"] != "INFO REQUESTED":
        flash("This request is not waiting for more information.", "warning")
    elif len(message) < 3:
        flash("Please type your reply.", "danger")
    else:
        db.execute(f"UPDATE {TABLES[rtype]} SET customer_reply = ? WHERE id = ?", (message, rid))
        set_status(db, rtype, rid, "UNDER REVIEW", note="Customer replied: " + message)
        notify_admins(db, f"{req['customer_name']} replied on {req['code']}.",
                      url_for("admin_request_details", rtype=rtype, rid=rid))
        db.commit()
        flash("Your reply was sent to the admin.", "success")
    return redirect(url_for("request_details", rtype=rtype, rid=rid))


@app.route("/refunds")
@login_required("customer")
def my_refunds():
    rows = get_db().execute(
        """SELECT f.*, r.request_code, r.id AS request_pk, p.name AS product_name
             FROM refunds f
             JOIN return_requests r ON r.id = f.request_id
             JOIN products p ON p.id = r.product_id
            WHERE r.user_id = ? ORDER BY f.id DESC""", (current_user()["id"],)).fetchall()
    return render_template("my_refunds.html", refunds=[dict(r) for r in rows])


# ======================================================================
# ADMIN
# ======================================================================
@app.route("/admin")
@login_required("admin")
def admin_dashboard():
    db = get_db()

    def count(sql, params=()):
        return db.execute(sql, params).fetchone()[0]

    def in_group(group, table=None):
        sts = STATUS_GROUPS[group]
        base = f"({UNION_SQL})" if table is None else table
        return count(f"SELECT COUNT(*) FROM {base} WHERE status IN ({status_placeholders(sts)})", sts)

    stats = {
        "customers": count("SELECT COUNT(*) FROM users WHERE role = 'customer'"),
        "products": count("SELECT COUNT(*) FROM products"),
        "total": count("SELECT (SELECT COUNT(*) FROM return_requests) + (SELECT COUNT(*) FROM exchange_requests)"),
        "pending": in_group("pending"),
        "approved_returns": in_group("approved", "return_requests"),
        "completed_returns": count("SELECT COUNT(*) FROM return_requests WHERE status = 'COMPLETED'"),
        "rejected": in_group("rejected"),
        "exchanges": count("SELECT COUNT(*) FROM exchange_requests"),
        "refunds": count("SELECT COUNT(*) FROM refunds WHERE status = 'Completed'"),
    }
    by_status = dict(db.execute(f"SELECT status, COUNT(*) FROM ({UNION_SQL}) GROUP BY status").fetchall())
    by_reason = dict(db.execute(f"SELECT reason, COUNT(*) FROM ({UNION_SQL}) GROUP BY reason "
                                "ORDER BY COUNT(*) DESC").fetchall())
    # last 6 months
    months, first = [], date.today().replace(day=1)
    for i in range(5, -1, -1):
        y, m = first.year, first.month - i
        while m <= 0:
            m += 12
            y -= 1
        months.append(f"{y}-{m:02d}")
    monthly_raw = dict(db.execute(
        f"SELECT substr(created_at, 1, 7), COUNT(*) FROM ({UNION_SQL}) GROUP BY 1").fetchall())
    charts = {
        "status": {"labels": [s for s in STATUSES if s in by_status], "values": [by_status[s] for s in STATUSES if s in by_status]},
        "types": {"labels": ["Returns", "Exchanges"],
                  "values": [count("SELECT COUNT(*) FROM return_requests"), stats["exchanges"]]},
        "reasons": {"labels": list(by_reason.keys()), "values": list(by_reason.values())},
        "monthly": {"labels": [datetime.strptime(m, "%Y-%m").strftime("%b %Y") for m in months],
                    "values": [monthly_raw.get(m, 0) for m in months]},
    }
    recent = list_requests(db, limit=6)
    return render_template("admin_dashboard.html", stats=stats, charts=charts, recent=recent)


@app.route("/admin/customers")
@login_required("admin")
def admin_customers():
    q = request.args.get("q", "").strip()
    sql = """SELECT u.*,
                    (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS order_count,
                    (SELECT COUNT(*) FROM return_requests r WHERE r.user_id = u.id) +
                    (SELECT COUNT(*) FROM exchange_requests e WHERE e.user_id = u.id) AS request_count
               FROM users u WHERE u.role = 'customer'"""
    params = []
    if q:
        sql += " AND (u.name LIKE ? OR u.email LIKE ? OR u.phone LIKE ?)"
        params = [f"%{q}%"] * 3
    rows = get_db().execute(sql + " ORDER BY u.id DESC", params).fetchall()
    return render_template("admin_customers.html", customers=[dict(r) for r in rows], q=q)


# ---- products
def product_form_values(form):
    return {
        "name": form.get("name", "").strip(),
        "category": form.get("category", "").strip(),
        "description": form.get("description", "").strip(),
        "price": to_float(form.get("price")),
        "return_window_days": to_int(form.get("return_window_days")),
        "stock": to_int(form.get("stock")),
        "exchange_available": 1 if form.get("exchange_available") else 0,
    }


def validate_product(v):
    errors = []
    if len(v["name"]) < 2:
        errors.append("Product name is required.")
    if not v["category"]:
        errors.append("Category is required.")
    if v["price"] is None or v["price"] < 0:
        errors.append("Enter a valid price.")
    if v["return_window_days"] is None or not 0 <= v["return_window_days"] <= 365:
        errors.append("Return window must be between 0 and 365 days.")
    if v["stock"] is None or v["stock"] < 0:
        errors.append("Enter a valid stock quantity.")
    return errors


@app.route("/admin/products")
@login_required("admin")
def admin_products():
    q = request.args.get("q", "").strip()
    sql, params = "SELECT * FROM products WHERE 1=1", []
    if q:
        sql += " AND (name LIKE ? OR category LIKE ?)"
        params = [f"%{q}%"] * 2
    rows = get_db().execute(sql + " ORDER BY id DESC", params).fetchall()
    return render_template("admin_products.html", items=[dict(r) for r in rows], q=q)


@app.route("/admin/products/new", methods=["GET", "POST"])
@login_required("admin")
def admin_product_new():
    form = {"return_window_days": 30, "stock": 10, "exchange_available": 1}
    if request.method == "POST":
        v = product_form_values(request.form)
        form = v
        errors = validate_product(v)
        image = None
        if not errors:
            image, err = save_image(request.files.get("image"), app.config["PRODUCT_UPLOAD_FOLDER"])
            if err:
                errors.append(err)
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            db = get_db()
            db.execute(
                "INSERT INTO products (name, category, description, price, image, return_window_days, "
                "exchange_available, stock, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (v["name"], v["category"], v["description"], v["price"],
                 f"uploads/products/{image}" if image else "img/products/placeholder.svg",
                 v["return_window_days"], v["exchange_available"], v["stock"], now()))
            db.commit()
            flash("Product added.", "success")
            return redirect(url_for("admin_products"))
    return render_template("admin_product_form.html", form=form, product=None, categories=PRODUCT_CATEGORIES)


@app.route("/admin/products/<int:pid>/edit", methods=["GET", "POST"])
@login_required("admin")
def admin_product_edit(pid):
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not product:
        abort(404)
    product = dict(product)
    form = dict(product)
    if request.method == "POST":
        v = product_form_values(request.form)
        form = dict(v, image=product["image"])
        errors = validate_product(v)
        image = None
        if not errors:
            image, err = save_image(request.files.get("image"), app.config["PRODUCT_UPLOAD_FOLDER"])
            if err:
                errors.append(err)
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            image_path = f"uploads/products/{image}" if image else product["image"]
            db.execute(
                "UPDATE products SET name=?, category=?, description=?, price=?, image=?, "
                "return_window_days=?, exchange_available=?, stock=? WHERE id=?",
                (v["name"], v["category"], v["description"], v["price"], image_path,
                 v["return_window_days"], v["exchange_available"], v["stock"], pid))
            db.commit()
            if image and product["image"].startswith("uploads/products/"):  # remove the old upload
                old = os.path.join(app.root_path, "static", product["image"])
                if os.path.isfile(old):
                    os.remove(old)
            flash("Product updated.", "success")
            return redirect(url_for("admin_products"))
    return render_template("admin_product_form.html", form=form, product=product, categories=PRODUCT_CATEGORIES)


@app.route("/admin/products/<int:pid>/delete", methods=["POST"])
@login_required("admin")
def admin_product_delete(pid):
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not product:
        abort(404)
    used = db.execute(
        "SELECT (SELECT COUNT(*) FROM orders WHERE product_id = ?) + "
        "(SELECT COUNT(*) FROM return_requests WHERE product_id = ?) + "
        "(SELECT COUNT(*) FROM exchange_requests WHERE product_id = ? OR replacement_product_id = ?)",
        (pid, pid, pid, pid)).fetchone()[0]
    if used:
        flash("This product has orders/requests, so it cannot be deleted. Set its stock to 0 instead.", "warning")
    else:
        db.execute("DELETE FROM products WHERE id = ?", (pid,))
        db.commit()
        if product["image"] and product["image"].startswith("uploads/products/"):
            path = os.path.join(app.root_path, "static", product["image"])
            if os.path.isfile(path):
                os.remove(path)
        flash("Product deleted.", "success")
    return redirect(url_for("admin_products"))


# ---- requests
@app.route("/admin/requests")
@login_required("admin")
def admin_requests():
    db = get_db()
    flt = request.args.get("filter", "all")
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    where, params = [], []
    if flt in ("return", "exchange"):
        where.append("q.rtype = ?")
        params.append(flt)
    elif flt in STATUS_GROUPS:
        where.append(f"q.status IN ({status_placeholders(STATUS_GROUPS[flt])})")
        params += STATUS_GROUPS[flt]
    if status in STATUSES:
        where.append("q.status = ?")
        params.append(status)
    if q:
        where.append("(q.code LIKE ? OR u.name LIKE ? OR p.name LIKE ?)")
        params += [f"%{q}%"] * 3
    if valid_date(date_from):
        where.append("substr(q.created_at, 1, 10) >= ?")
        params.append(date_from)
    if valid_date(date_to):
        where.append("substr(q.created_at, 1, 10) <= ?")
        params.append(date_to)
    items = list_requests(db, ("WHERE " + " AND ".join(where)) if where else "", params)
    return render_template("admin_requests.html", items=items, flt=flt, status=status, q=q,
                           date_from=date_from, date_to=date_to, statuses=STATUSES)


@app.route("/admin/requests/<rtype>/<int:rid>")
@login_required("admin")
def admin_request_details(rtype, rid):
    check_rtype(rtype)
    db = get_db()
    req = fetch_request(db, rtype, rid)
    if not req:
        abort(404)
    history, timeline, refund, exchange = request_extras(db, req)
    return render_template(
        "admin_request_details.html", req=req, history=history, timeline=timeline, refund=refund,
        exchange=exchange, available=available_actions(rtype, req["status"]),
        refund_methods=REFUND_METHODS, refund_statuses=["Pending", "Processing"],
        exchange_statuses=EXCHANGE_STATUSES[1:5])


@app.route("/admin/requests/<rtype>/<int:rid>/action", methods=["POST"])
@login_required("admin")
def admin_request_action(rtype, rid):
    check_rtype(rtype)
    db = get_db()
    req = fetch_request(db, rtype, rid)
    if not req:
        abort(404)
    back = redirect(url_for("admin_request_details", rtype=rtype, rid=rid))
    action = request.form.get("action", "")
    remarks = request.form.get("remarks", "").strip()[:500]
    code, uid, status = req["code"], req["user_id"], req["status"]
    link = url_for("request_details", rtype=rtype, rid=rid)

    # --- actions that do not change the main request status ------------
    if action == "add_remark":
        if not remarks:
            flash("Please type a remark first.", "warning")
            return back
        db.execute(f"UPDATE {TABLES[rtype]} SET admin_remarks = ?, updated_at = ? WHERE id = ?",
                   (remarks, now(), rid))
        add_history(db, rtype, rid, status, "Admin remark: " + remarks)
        notify(db, uid, f"Admin added a remark on {code}: {remarks}", link)
        db.commit()
        flash("Remark saved and customer notified.", "success")
        return back

    if action == "update_refund_status":
        new = request.form.get("refund_status", "")
        if status != "REFUND INITIATED" or new not in ("Pending", "Processing"):
            flash("Refund status cannot be changed now.", "danger")
            return back
        db.execute("UPDATE refunds SET status = ? WHERE request_id = ?", (new, rid))
        add_history(db, rtype, rid, status, f"Refund status set to {new}")
        notify(db, uid, f"Refund status for {code} is now '{new}'.", link)
        db.commit()
        flash(f"Refund status updated to {new}.", "success")
        return back

    if action == "update_exchange_status":
        new = request.form.get("exchange_status", "")
        row = db.execute("SELECT * FROM exchanges WHERE request_id = ?", (rid,)).fetchone()
        allowed = EXCHANGE_STATUSES[1:5]
        if status != "EXCHANGE PROCESSING" or not row or new not in allowed \
                or EXCHANGE_STATUSES.index(new) < EXCHANGE_STATUSES.index(row["status"]):
            flash("Exchange status cannot be changed like that.", "danger")
            return back
        shipping = "Delivered" if new == "Delivered" else "Shipped" if new == "Shipped" else "Not Shipped"
        db.execute("UPDATE exchanges SET status = ?, shipping_status = ? WHERE request_id = ?", (new, shipping, rid))
        add_history(db, rtype, rid, status, f"Exchange status set to {new}")
        notify(db, uid, f"Exchange status for {code} is now '{new}'.", link)
        db.commit()
        flash(f"Exchange status updated to {new}.", "success")
        return back

    # --- actions that move the request to a new status -----------------
    if action not in available_actions(rtype, status):
        flash("That action is not allowed at the current status.", "danger")
        return back

    if action == "start_review":
        set_status(db, rtype, rid, "UNDER REVIEW", remarks=remarks)
        notify(db, uid, f"Your request {code} is now under review.", link)

    elif action == "approve":
        set_status(db, rtype, rid, "APPROVED", remarks=remarks)
        if rtype == "exchange" and not db.execute("SELECT 1 FROM exchanges WHERE request_id = ?", (rid,)).fetchone():
            db.execute(
                "INSERT INTO exchanges (exchange_code, request_id, original_product_id, replacement_product_id, "
                "status, shipping_status, created_at) VALUES (?,?,?,?, 'Pending', 'Not Shipped', ?)",
                (next_code(db, "exchanges", "exchange_code", "EXCH"), rid, req["product_id"],
                 req["replacement_product_id"], now()))
        notify(db, uid, f"Good news! Your request {code} has been approved.", link)

    elif action == "reject":
        if not remarks:
            flash("Please enter a remark explaining the rejection.", "danger")
            return back
        set_status(db, rtype, rid, "REJECTED", remarks=remarks)
        notify(db, uid, f"Your request {code} was rejected. Reason: {remarks}", link)

    elif action == "request_info":
        if not remarks:
            flash("Please write what information you need from the customer.", "danger")
            return back
        set_status(db, rtype, rid, "INFO REQUESTED", remarks=remarks)
        notify(db, uid, f"More information is needed for {code}: {remarks}", link)

    elif action == "schedule_pickup":
        pickup = valid_date(request.form.get("pickup_date", ""))
        if not pickup or pickup < date.today():
            flash("Choose a valid pickup date (today or later).", "danger")
            return back
        db.execute(f"UPDATE {TABLES[rtype]} SET pickup_scheduled_date = ? WHERE id = ?", (pickup.isoformat(), rid))
        set_status(db, rtype, rid, "PICKUP SCHEDULED",
                   note=f"Pickup scheduled for {pickup.strftime('%d %b %Y')}", remarks=remarks)
        notify(db, uid, f"Pickup scheduled for {pickup.strftime('%d %b %Y')} for request {code}.", link)

    elif action == "mark_picked_up":
        set_status(db, rtype, rid, "PICKED UP", remarks=remarks)
        notify(db, uid, f"Your product for request {code} has been picked up.", link)

    elif action == "quality_check":
        set_status(db, rtype, rid, "QUALITY CHECK", note=remarks or "Quality check completed", remarks=remarks)
        notify(db, uid, f"Quality check completed for request {code}.", link)

    elif action == "initiate_refund":
        amount = to_float(request.form.get("amount"))
        method = request.form.get("method", "")
        if amount is None or amount <= 0 or amount > req["order_amount"] + 0.001:
            flash(f"Refund amount must be between 0 and {money(req['order_amount'])}.", "danger")
            return back
        if method not in REFUND_METHODS:
            flash("Choose a refund method.", "danger")
            return back
        ref_code = next_code(db, "refunds", "refund_code", "REF")
        db.execute("INSERT INTO refunds (refund_code, request_id, amount, method, status, created_at) "
                   "VALUES (?,?,?,?, 'Pending', ?)", (ref_code, rid, amount, method, now()))
        set_status(db, rtype, rid, "REFUND INITIATED",
                   note=f"Refund {ref_code} of {money(amount)} initiated via {method}", remarks=remarks)
        notify(db, uid, f"Refund {ref_code} of {money(amount)} has been initiated for {code}.", link)

    elif action == "process_exchange":
        row = db.execute("SELECT * FROM exchanges WHERE request_id = ?", (rid,)).fetchone()
        if not row:  # safety net
            db.execute(
                "INSERT INTO exchanges (exchange_code, request_id, original_product_id, replacement_product_id, "
                "status, shipping_status, created_at) VALUES (?,?,?,?, 'Pending', 'Not Shipped', ?)",
                (next_code(db, "exchanges", "exchange_code", "EXCH"), rid, req["product_id"],
                 req["replacement_product_id"], now()))
        cur = db.execute("UPDATE products SET stock = stock - 1 WHERE id = ? AND stock > 0",
                         (req["replacement_product_id"],))
        if cur.rowcount == 0:
            db.rollback()
            flash("The replacement product is out of stock. Update the stock first.", "danger")
            return back
        db.execute("UPDATE exchanges SET status = 'Approved' WHERE request_id = ?", (rid,))
        set_status(db, rtype, rid, "EXCHANGE PROCESSING",
                   note=f"Replacement '{req['replacement_name']}' reserved", remarks=remarks)
        notify(db, uid, f"Your exchange {code} is being processed. A replacement will be shipped soon.", link)

    elif action == "mark_completed":
        if rtype == "return":
            db.execute("UPDATE refunds SET status = 'Completed', completed_at = ? WHERE request_id = ?", (now(), rid))
        else:
            row = db.execute("SELECT status FROM exchanges WHERE request_id = ?", (rid,)).fetchone()
            if not row or row["status"] != "Delivered":
                flash("Set the exchange status to 'Delivered' before completing it.", "danger")
                return back
            db.execute("UPDATE exchanges SET status = 'Completed', completed_at = ? WHERE request_id = ?",
                       (now(), rid))
        set_status(db, rtype, rid, "COMPLETED", remarks=remarks)
        notify(db, uid, f"Your request {code} has been completed. Thank you!", link)

    db.commit()
    flash("Request updated and customer notified.", "success")
    return back


# ---- notifications
@app.route("/admin/notifications", methods=["GET", "POST"])
@login_required("admin")
def admin_notifications():
    db = get_db()
    if request.method == "POST":
        message = request.form.get("message", "").strip()[:300]
        target = request.form.get("target", "")
        if len(message) < 3:
            flash("Please write a message.", "danger")
        elif target == "all":
            for c in db.execute("SELECT id FROM users WHERE role = 'customer'").fetchall():
                notify(db, c["id"], message)
            db.commit()
            flash("Announcement sent to all customers.", "success")
        elif db.execute("SELECT 1 FROM users WHERE id = ? AND role = 'customer'", (to_int(target),)).fetchone():
            notify(db, to_int(target), message)
            db.commit()
            flash("Notification sent.", "success")
        else:
            flash("Choose who should receive the message.", "danger")
        return redirect(url_for("admin_notifications"))
    items = [dict(r) for r in db.execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 100", (current_user()["id"],))]
    customers = [dict(r) for r in db.execute("SELECT id, name, email FROM users WHERE role = 'customer' ORDER BY name")]
    return render_template("admin_notifications.html", items=items, customers=customers)


# ======================================================================
# ERROR PAGES
# ======================================================================
@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400, title="Invalid request",
                           message="The form has expired or is invalid. Please go back, refresh the page and try again."), 400


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, title="Access denied",
                           message="You do not have permission to open this page."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(413)
def too_large(e):
    return render_template("error.html", code=413, title="File too large",
                           message="The uploaded file is larger than 5 MB. Please choose a smaller image."), 413


@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500


if __name__ == "__main__":
    app.run(debug=True)
