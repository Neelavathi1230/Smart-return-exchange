"""Project configuration. Change values here, not inside app.py."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Used to sign the session cookie. In a real deployment set the SECRET_KEY
    # environment variable to a long random string.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-key-change-me")

    # SQLite database file (created automatically on first run)
    DATABASE = os.path.join(BASE_DIR, "instance", "database.db")

    # Product images are public (inside static/). Evidence images uploaded by
    # customers are kept OUTSIDE static/ and served only to the owner/admin.
    PRODUCT_UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "products")
    EVIDENCE_UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "evidence")

    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB upload limit
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

    CURRENCY = "₹"

    # DEMO admin account (created automatically). CHANGE IN A REAL DEPLOYMENT!
    ADMIN_NAME = "System Admin"
    ADMIN_EMAIL = "admin@smartreturn.com"
    ADMIN_PASSWORD = "admin123"

    # Adds a demo customer + sample orders/requests so the dashboards are not
    # empty on the first run. Set to False for a completely clean database.
    SEED_DEMO_DATA = True
    DEMO_CUSTOMER_EMAIL = "demo@customer.com"
    DEMO_CUSTOMER_PASSWORD = "customer123"

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
