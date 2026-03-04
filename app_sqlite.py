import os
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    g,
    session,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "home_inventory.db")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_db():
    if "db" not in g:
        g.db = get_db_connection()
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            barcode TEXT,
            category TEXT,
            location TEXT,
            expiry_date TEXT,
            quantity REAL DEFAULT 0,
            min_quantity REAL DEFAULT 0,
            added_date TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )

    # Add unit column for existing databases (ignore error if it already exists)
    try:
        cursor.execute("ALTER TABLE items ADD COLUMN unit TEXT")
    except sqlite3.OperationalError:
        pass

    # Leave users empty; they will be created via registration

    conn.commit()
    conn.close()


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")
app.teardown_appcontext(close_db)


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapper


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    return dict(row) if row else None


@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.current_user = None
    else:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        cursor.close()
        g.current_user = dict(row) if row else None


def send_email(subject: str, body: str, to_email: str):
    import smtplib
    from email.mime.text import MIMEText

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    from_email = os.environ.get("FROM_EMAIL", smtp_user)

    if not (smtp_host and smtp_user and smtp_password and from_email and to_email):
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    except Exception:
        pass


def check_expiry_and_refill():
    reminder_days = int(os.environ.get("REMINDER_DAYS", "3"))
    try:
        today = datetime.utcnow().date()
        limit_date = today + timedelta(days=reminder_days)

        db = get_db_connection()
        cursor = db.cursor()

        # Get all users with an email; send per-user summaries
        cursor.execute("SELECT id, email, name FROM users WHERE email IS NOT NULL")
        users = cursor.fetchall()

        for user in users:
            user_id = user["id"]
            to_email = user["email"]
            if not to_email:
                continue

            cursor.execute(
                """
                SELECT * FROM items
                WHERE user_id = ?
                  AND expiry_date IS NOT NULL
                  AND expiry_date <= ?
                ORDER BY expiry_date ASC
                """,
                (user_id, limit_date.isoformat()),
            )
            expiring_items = cursor.fetchall()

            cursor.execute(
                """
                SELECT * FROM items
                WHERE user_id = ?
                  AND quantity IS NOT NULL
                  AND min_quantity IS NOT NULL
                  AND quantity <= min_quantity
                ORDER BY quantity ASC
                """,
                (user_id,),
            )
            low_stock_items = cursor.fetchall()

            if not expiring_items and not low_stock_items:
                continue

            lines = [f"Hello {user['name']}!", ""]
            if expiring_items:
                lines.append("Items expiring soon:")
                for item in expiring_items:
                    exp_date = item["expiry_date"]
                    lines.append(f"- {item['product_name']} (expires {exp_date})")
                lines.append("")

            if low_stock_items:
                lines.append("Items that need refill:")
                for item in low_stock_items:
                    lines.append(
                        f"- {item['product_name']} (qty {item['quantity']}, min {item['min_quantity']})"
                    )

            body = "\n".join(lines)
            subject = "Home Inventory Alerts"
            send_email(subject, body, to_email)
    except Exception:
        pass


scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(check_expiry_and_refill, "interval", hours=24)
scheduler.start()


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    cursor = db.cursor()

    user_id = session["user_id"]

    cursor.execute("SELECT COUNT(*) AS total FROM items WHERE user_id = ?", (user_id,))
    total_items = cursor.fetchone()["total"]

    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM items
        WHERE user_id = ?
          AND expiry_date IS NOT NULL
          AND expiry_date <= ?
        """,
        (
            user_id,
            (datetime.utcnow().date() + timedelta(days=3)).isoformat(),
        ),
    )
    expiring_soon = cursor.fetchone()["cnt"]

    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM items
        WHERE user_id = ?
          AND quantity IS NOT NULL
          AND min_quantity IS NOT NULL
          AND quantity <= min_quantity
        """,
        (user_id,),
    )
    low_stock = cursor.fetchone()["cnt"]

    # Load a few items for quick-use panel
    cursor.execute(
        """
        SELECT id, product_name, quantity, unit
        FROM items
        WHERE user_id = ?
        ORDER BY product_name ASC
        LIMIT 20
        """,
        (user_id,),
    )
    quick_items = [dict(row) for row in cursor.fetchall()]

    cursor.close()

    return render_template(
        "dashboard.html",
        total_items=total_items,
        expiring_soon=expiring_soon,
        low_stock=low_stock,
        quick_items=quick_items,
    )


@app.route("/scan")
def scan():
    return render_template("scan.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not name or not email or not password:
            flash("All fields are required.", "danger")
            return redirect(url_for("register"))

        db = get_db()
        cursor = db.cursor()

        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        existing = cursor.fetchone()
        if existing:
            flash("An account with this email already exists.", "warning")
            cursor.close()
            return redirect(url_for("login"))

        cursor.execute(
            "INSERT INTO users (name, email, password) VALUES (?,?,?)",
            (name, email, password),
        )
        db.commit()
        user_id = cursor.lastrowid
        cursor.close()

        session["user_id"] = user_id
        flash("Account created and logged in.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        cursor.close()

        if row is None or row["password"] != password:
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        session["user_id"] = row["id"]
        flash("Logged in successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/get_product/<barcode>")
def get_product(barcode):
    try:
        url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        if data.get("status") == 1:
            product_name = data["product"].get("product_name", "") or ""
        else:
            product_name = ""
        return jsonify({"success": True, "product_name": product_name, "barcode": barcode})
    except Exception:
        return jsonify({"success": False, "product_name": "", "barcode": barcode})


@app.route("/items")
@login_required
def items():
    filter_type = request.args.get("filter", "all")
    db = get_db()
    cursor = db.cursor()

    user_id = session["user_id"]

    base_query = "SELECT * FROM items WHERE user_id = ?"
    conditions = []
    params = []

    today = datetime.utcnow().date()
    soon_limit = today + timedelta(days=3)

    if filter_type == "expiring":
        conditions.append("expiry_date IS NOT NULL AND expiry_date <= ?")
        params.append(soon_limit.isoformat())
    elif filter_type == "low_stock":
        conditions.append(
            "quantity IS NOT NULL AND min_quantity IS NOT NULL AND quantity <= min_quantity"
        )

    params = [user_id] + params

    if conditions:
        base_query += " AND " + " AND ".join(conditions)

    base_query += " ORDER BY expiry_date IS NULL, expiry_date ASC"

    cursor.execute(base_query, tuple(params))
    rows = cursor.fetchall()
    cursor.close()

    items_list = []
    for row in rows:
        item = dict(row)
        expiry_raw = item.get("expiry_date")
        if expiry_raw:
            try:
                item["expiry_date"] = datetime.strptime(expiry_raw, "%Y-%m-%d").date()
            except ValueError:
                item["expiry_date"] = None
        else:
            item["expiry_date"] = None

        unit = (item.get("unit") or "count").lower()
        qty = float(item.get("quantity") or 0)
        if unit in ("kg", "g", "ml", "l"):
            item["quantity_display"] = f"{qty:g} {unit}"
        else:
            item["quantity_display"] = f"{qty:g}"

        items_list.append(item)

    return render_template("items.html", items=items_list, filter_type=filter_type)


@app.route("/add_item", methods=["GET", "POST"])
@login_required
def add_item():
    if request.method == "POST":
        product_name = request.form.get("product_name", "").strip()
        barcode = request.form.get("barcode", "").strip()
        category = request.form.get("category", "").strip()
        expiry = request.form.get("expiry_date", "").strip()
        quantity = request.form.get("quantity", "0").strip()
        min_quantity = request.form.get("min_quantity", "0").strip()
        location = request.form.get("location", "").strip()
        unit = request.form.get("unit", "count").strip().lower()

        expiry_date = None
        if expiry:
            try:
                expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid expiry date format.", "danger")

        try:
            quantity_val = float(quantity) if quantity else 0.0
        except ValueError:
            quantity_val = 0.0

        try:
            min_quantity_val = float(min_quantity) if min_quantity else 0.0
        except ValueError:
            min_quantity_val = 0.0

        if not product_name:
            flash("Product name is required.", "danger")
            return redirect(url_for("add_item"))

        db = get_db()
        cursor = db.cursor()

        user_id = session["user_id"]

        added_date = datetime.utcnow().date().isoformat()
        expiry_value = expiry_date.isoformat() if expiry_date else None

        cursor.execute(
            """
            INSERT INTO items
                (user_id, product_name, barcode, category, expiry_date,
                 quantity, min_quantity, location, added_date, unit)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id,
                product_name,
                barcode or None,
                category or None,
                expiry_value,
                quantity_val,
                min_quantity_val,
                location or None,
                added_date,
                unit or "count",
            ),
        )
        db.commit()
        cursor.close()
        flash("Item added successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_item.html")


@app.route("/alerts")
@login_required
def alerts():
    db = get_db()
    cursor = db.cursor()

    user_id = session["user_id"]
    limit_date = datetime.utcnow().date() + timedelta(days=3)
    cursor.execute(
        """
        SELECT * FROM items
        WHERE user_id = ?
          AND expiry_date IS NOT NULL
          AND expiry_date <= ?
        ORDER BY expiry_date ASC
        """,
        (user_id, limit_date.isoformat()),
    )
    rows = cursor.fetchall()
    cursor.close()

    items_list = []
    for row in rows:
        item = dict(row)
        expiry_raw = item.get("expiry_date")
        if expiry_raw:
            try:
                item["expiry_date"] = datetime.strptime(expiry_raw, "%Y-%m-%d").date()
            except ValueError:
                item["expiry_date"] = None
        else:
            item["expiry_date"] = None
        items_list.append(item)

    return render_template("alerts.html", items=items_list)


@app.route("/refill")
@login_required
def refill():
    db = get_db()
    cursor = db.cursor()
    user_id = session["user_id"]
    cursor.execute(
        """
        SELECT * FROM items
        WHERE user_id = ?
          AND quantity IS NOT NULL
          AND min_quantity IS NOT NULL
          AND quantity <= min_quantity
        ORDER BY quantity ASC
        """,
        (user_id,),
    )
    items_list = [dict(row) for row in cursor.fetchall()]
    cursor.close()
    return render_template("refill.html", items=items_list)


@app.route("/update_quantity/<int:item_id>", methods=["POST"])
@login_required
def update_quantity(item_id):
    new_quantity = request.form.get("quantity", "").strip()
    try:
        quantity_val = float(new_quantity)
    except ValueError:
        flash("Invalid quantity value.", "danger")
        return redirect(url_for("items"))

    db = get_db()
    cursor = db.cursor()
    user_id = session["user_id"]
    cursor.execute(
        "UPDATE items SET quantity = ? WHERE id = ? AND user_id = ?",
        (quantity_val, item_id, user_id),
    )
    db.commit()
    cursor.close()
    flash("Quantity updated.", "success")
    return redirect(url_for("items"))


@app.route("/delete_item/<int:item_id>", methods=["POST"])
@login_required
def delete_item(item_id):
    db = get_db()
    cursor = db.cursor()
    user_id = session["user_id"]
    cursor.execute("DELETE FROM items WHERE id = ? AND user_id = ?", (item_id, user_id))
    db.commit()
    cursor.close()
    flash("Item deleted.", "info")
    return redirect(url_for("items"))


def convert_amount_to_item_unit(amount, amount_unit, item_unit: str):
    au = (amount_unit or item_unit or "count").lower()
    iu = (item_unit or "count").lower()

    # Mass conversion
    if iu == "kg":
        if au == "g":
            return amount / 1000.0
        return amount
    if iu == "g":
        if au == "kg":
            return amount * 1000.0
        return amount

    # Volume conversion
    if iu == "l":
        if au == "ml":
            return amount / 1000.0
        return amount
    if iu == "ml":
        if au == "l":
            return amount * 1000.0
        return amount

    # Count or unsupported combination – no conversion
    return amount


@app.route("/use_item/<int:item_id>", methods=["POST"])
@login_required
def use_item(item_id):
    amount_str = request.form.get("amount", "").strip()
    amount_unit = request.form.get("amount_unit", "").strip().lower() or None
    try:
        amount = float(amount_str)
    except ValueError:
        flash("Invalid amount.", "danger")
        return redirect(url_for("items"))

    if amount <= 0:
        flash("Amount must be greater than zero.", "warning")
        return redirect(url_for("items"))

    db = get_db()
    cursor = db.cursor()
    user_id = session["user_id"]
    cursor.execute(
        "SELECT quantity, unit FROM items WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        flash("Item not found.", "danger")
        return redirect(url_for("items"))

    current_qty = float(row["quantity"] or 0)
    item_unit = (row["unit"] or "count").lower()

    used_in_item_unit = convert_amount_to_item_unit(amount, amount_unit, item_unit)
    new_qty = max(current_qty - used_in_item_unit, 0.0)

    cursor.execute(
        "UPDATE items SET quantity = ? WHERE id = ? AND user_id = ?",
        (new_qty, item_id, user_id),
    )
    db.commit()
    cursor.close()

    flash("Usage recorded and quantity updated.", "success")
    return redirect(url_for("items"))


@app.route("/quick_use", methods=["POST"])
@login_required
def quick_use():
    item_id_str = request.form.get("item_id", "").strip()
    amount_str = request.form.get("amount", "").strip()
    amount_unit = request.form.get("amount_unit", "").strip().lower() or None

    try:
        item_id = int(item_id_str)
    except ValueError:
        flash("Invalid item selected.", "danger")
        return redirect(url_for("dashboard"))

    try:
        amount = float(amount_str)
    except ValueError:
        flash("Invalid amount.", "danger")
        return redirect(url_for("dashboard"))

    if amount <= 0:
        flash("Amount must be greater than zero.", "warning")
        return redirect(url_for("dashboard"))

    db = get_db()
    cursor = db.cursor()
    user_id = session["user_id"]
    cursor.execute(
        "SELECT quantity, unit FROM items WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        flash("Item not found.", "danger")
        return redirect(url_for("dashboard"))

    current_qty = float(row["quantity"] or 0)
    item_unit = (row["unit"] or "count").lower()
    used_in_item_unit = convert_amount_to_item_unit(amount, amount_unit, item_unit)
    new_qty = max(current_qty - used_in_item_unit, 0.0)

    cursor.execute(
        "UPDATE items SET quantity = ? WHERE id = ? AND user_id = ?",
        (new_qty, item_id, user_id),
    )
    db.commit()
    cursor.close()

    flash("Usage recorded and quantity updated.", "success")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)

