from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
from flask_session import Session
import sqlite3
from functools import wraps
import os
import base64
import shutil
from datetime import datetime
import requests
from requests.auth import HTTPBasicAuth

app = Flask(__name__)

# -------------------------
# Config Session
# -------------------------
app.secret_key = "supersecretkey"
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

DB_NAME = "attendance.db"
FACES_DIR = os.path.join("static", "faces")
# -------------------------
# Attendance Protection Config  ✅ NEW
# -------------------------
LAST_FACE_HIT = {}
FACE_COOLDOWN_SECONDS = 60     # same face ignore for 1 minute
MIN_CHECKOUT_MINUTES = 10      # checkout only after 10 minutes
# -------------------------
# ERPNext Config
# -------------------------
ERP_URL = "https://erp.meerakunj.com"
ERP_API_KEY = "7a0160cc0af6669"
ERP_API_SECRET = "010180d50906ee5"
def push_to_erpnext(employee_name, branch, log_type):
    """
    log_type: IN / OUT
    """
    url = f"{ERP_URL}/api/resource/Employee Checkin"

    payload = {
        "employee": employee_name,
        "log_type": log_type,
        "location": branch,
        "device_id": branch
    }

    try:
        res = requests.post(
            url,
            json=payload,
            auth=HTTPBasicAuth(ERP_API_KEY, ERP_API_SECRET),
            timeout=10
        )

        if res.status_code in (200, 201):
            return True, res.json()
        else:
            return False, res.text

    except Exception as e:
        return False, str(e)


# -------------------------
# Helper Functions
# -------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def create_default_admin():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
    """)
    admin = conn.execute("SELECT * FROM users").fetchone()
    if not admin:
        conn.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            ("admin", "0000")
        )
        conn.commit()
    conn.close()

def create_tables():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id TEXT UNIQUE,
            name TEXT,
            branch TEXT,
            phone TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT
        )
    """)
    conn.commit()
    conn.close()

def generate_employee_id():
    conn = get_db_connection()
    last = conn.execute(
        "SELECT emp_id FROM employees ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if last and last["emp_id"].startswith("HR-EMP-"):
        num = int(last["emp_id"].split("-")[-1]) + 1
    else:
        num = 1

    return f"HR-EMP-{num:04d}"

# -------------------------
# Auth Guard
# -------------------------
def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrap

# -------------------------
# Routes
# -------------------------
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db_connection()
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (username, password)
        ).fetchone()
        conn.close()

        if user:
            session["user"] = username
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Invalid login")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

# -------------------------
# Admin update (Emergency user protection)
# -------------------------
@app.route('/update_admin', methods=['POST'])
@login_required
def update_admin():
    data = request.get_json()

    curr_password = data.get('current_password')
    new_username = data.get('new_username')
    new_password = data.get('new_password')
    current_user = session['user']

    if current_user.lower() == "mppl":
        return jsonify({"success": False, "message": "Emergency user cannot be modified!"})

    if not curr_password or not new_username or not new_password:
        return jsonify({"success": False, "message": "All fields are required!"})

    conn = get_db_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE username=? AND password=?",
        (current_user, curr_password)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({"success": False, "message": "Current password is incorrect!"})

    conn.execute(
        "UPDATE users SET username=?, password=? WHERE username=?",
        (new_username, new_password, current_user)
    )
    conn.commit()
    conn.close()

    session['user'] = new_username
    return jsonify({"success": True, "message": "Admin credentials updated successfully!"})

# -------------------------
# Dashboard
# -------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db_connection()

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM employees"
    ).fetchone()["cnt"]

    present = conn.execute("""
        SELECT COUNT(DISTINCT emp_id) as cnt
        FROM attendance
        WHERE DATE(timestamp) = DATE('now','localtime') AND status='Checkin'
    """).fetchone()["cnt"]

    absent = max(0, total - present) if present>0 else 0

    last = conn.execute("""
        SELECT e.name || ' (' || e.emp_id || ')' as emp_name
        FROM attendance a
        JOIN employees e ON a.emp_id = e.emp_id
        ORDER BY a.timestamp DESC LIMIT 1
    """).fetchone()

    last_detected = last["emp_name"] if last else "—"

    conn.close()

    return render_template(
        "dashboard.html",
        total=total,
        present=present,
        absent=absent,
        last_detected=last_detected
    )

# -------------------------
# Logs
# -------------------------
@app.route("/logs")
@login_required
def logs():
    conn = get_db_connection()

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM employees"
    ).fetchone()["cnt"]

    present_rows = conn.execute("""
        SELECT a.*, e.name, e.branch
        FROM attendance a
        JOIN employees e ON a.emp_id = e.emp_id
        WHERE DATE(a.timestamp) = DATE('now','localtime')
        ORDER BY a.timestamp DESC
    """).fetchall()

    present = len([r for r in present_rows if r["status"]=="Checkin"])
    absent = max(0, total - present) if present>0 else 0

    last_detected = (
        present_rows[0]["name"] + " • " + present_rows[0]["branch"]
        if present_rows else "—"
    )

    conn.close()

    return render_template(
        "logs.html",
        total=total,
        present=present,
        absent=absent,
        last_detected=last_detected,
        recent_logs=present_rows[:10]
    )

# -------------------------
# Get all registered faces
# -------------------------
@app.route("/get_registered_faces")
def get_registered_faces():
    conn = get_db_connection()
    rows = conn.execute("SELECT emp_id FROM employees").fetchall()
    conn.close()

    data = []
    for r in rows:
        emp_id = r["emp_id"]
        img_path = os.path.join(FACES_DIR, emp_id, f"{emp_id}_1.png")
        if os.path.exists(img_path):
            data.append({
                "emp_id": emp_id,
                "image": f"/static/faces/{emp_id}/{emp_id}_1.png"
            })
    return jsonify(data)

# -------------------------
# Camera page
# -------------------------
@app.route("/camera")
def camera():
    return render_template("camera.html")



#-----------------------------------
# -------------------------
# Recent Attendance API
# -------------------------
@app.route("/api/recent_attendance")
@login_required
def api_recent_attendance():
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT a.emp_id, e.name, e.branch, a.timestamp, a.status
        FROM attendance a
        JOIN employees e ON a.emp_id = e.emp_id
        ORDER BY a.timestamp DESC
        LIMIT 10
    """).fetchall()
    conn.close()

    data = []
    for r in rows:
        data.append({
            "emp_id": r["emp_id"],
            "name": r["name"],
            "branch": r["branch"],
            "timestamp": r["timestamp"],
            "status": r["status"]
        })
    return jsonify(data)

#-----------------------------------
@app.route("/api/system_health")
@login_required
def api_system_health():
    conn = get_db_connection()

    total = conn.execute("SELECT COUNT(*) as cnt FROM employees").fetchone()["cnt"]

    present = conn.execute("""
        SELECT COUNT(DISTINCT emp_id) as cnt
        FROM attendance
        WHERE DATE(timestamp) = DATE('now','localtime') AND status='Checkin'
    """).fetchone()["cnt"]

    absent = max(0, total - present) if present > 0 else total

    last_sync = datetime.now().strftime("%H:%M:%S")

    conn.close()

    return jsonify({
        "total": total,
        "present": present,
        "absent": absent,
        "last_sync": last_sync
    })

# -------------------------
# Attendance API (UPDATED LOGIC)
# -------------------------
@app.route("/mark_attendance", methods=["POST"])
def mark_attendance():
    data = request.get_json()
    emp_id = data.get("emp_id")

    if not emp_id:
        return jsonify({"success": False, "message": "Employee ID missing"})

    now = datetime.now()

    # ---- FACE COOLDOWN PROTECTION ----
    last_hit = LAST_FACE_HIT.get(emp_id)
    if last_hit:
        diff_sec = (now - last_hit).total_seconds()
        if diff_sec < FACE_COOLDOWN_SECONDS:
            return jsonify({
                "success": False,
                "message": "Please wait before next punch"
            })

    LAST_FACE_HIT[emp_id] = now

    conn = get_db_connection()

    emp = conn.execute(
        "SELECT * FROM employees WHERE emp_id=?",
        (emp_id,)
    ).fetchone()

    if not emp:
        conn.close()
        return jsonify({"success": False, "message": "Unauthorized person"})

    employee_name = emp["name"]
    branch = emp["branch"]

    records = conn.execute("""
        SELECT status, timestamp
        FROM attendance
        WHERE emp_id=? AND DATE(timestamp)=DATE('now','localtime')
        ORDER BY timestamp ASC
    """, (emp_id,)).fetchall()

    # -------------------------
    # CHECK-IN
    # -------------------------
    if len(records) == 0:
        conn.execute(
            "INSERT INTO attendance (emp_id, status) VALUES (?, 'Checkin')",
            (emp_id,)
        )
        conn.commit()
        conn.close()

        push_to_erpnext(employee_name, branch, "IN")

        return jsonify({
            "success": True,
            "status": "Checkin",
            "message": "Check-in successful"
        })

    # -------------------------
    # CHECK-OUT
    # -------------------------
    if len(records) == 1 and records[0]["status"] == "Checkin":
        last_time = datetime.fromisoformat(records[0]["timestamp"])
        diff_minutes = (now - last_time).total_seconds() / 60

        if diff_minutes < MIN_CHECKOUT_MINUTES:
            conn.close()
            return jsonify({
                "success": False,
                "message": f"Checkout allowed only after {MIN_CHECKOUT_MINUTES} minutes"
            })

        conn.execute(
            "INSERT INTO attendance (emp_id, status) VALUES (?, 'Checkout')",
            (emp_id,)
        )
        conn.commit()
        conn.close()

        push_to_erpnext(employee_name, branch, "OUT")

        return jsonify({
            "success": True,
            "status": "Checkout",
            "message": "Checkout successful"
        })

    conn.close()
    return jsonify({
        "success": False,
        "message": "Attendance already completed for today"
    })

# -------------------------
# Other routes
# -------------------------
@app.route("/register")
@login_required
def register():
    return render_template("register.html")

@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html")

@app.route("/employees")
@login_required
def employees_page():
    return render_template("employee_list.html")

@app.route("/edit_employee/<int:id>")
@login_required
def edit_employee_page(id):
    conn = get_db_connection()
    emp = conn.execute(
        "SELECT * FROM employees WHERE id=?",
        (id,)
    ).fetchone()
    conn.close()
    if not emp:
        return "Employee not found", 404
    return render_template("edit_employee.html", employee=emp)

@app.route("/api/employees")
@login_required
def api_employees():
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT id, emp_id, name, branch, phone
        FROM employees
        ORDER BY id DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/register_employee", methods=["POST"])
@login_required
def register_employee():
    data = request.get_json()
    name = data.get("name")
    branch = data.get("branch")
    phone = data.get("phone")

    if not name or not branch:
        return jsonify({"success": False, "message": "Name and Branch required"})

    emp_id = generate_employee_id()
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO employees (emp_id, name, branch, phone)
        VALUES (?, ?, ?, ?)
    """, (emp_id, name, branch, phone))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "emp_id": emp_id, "message": f"Employee {emp_id} registered"})

@app.route("/update_employee/<int:id>", methods=["POST"])
@login_required
def update_employee(id):
    data = request.get_json()
    conn = get_db_connection()
    conn.execute("""
        UPDATE employees
        SET name=?, branch=?, phone=?
        WHERE id=?
    """, (data.get("name"), data.get("branch"), data.get("phone"), id))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Employee updated successfully"})

@app.route("/delete_employee/<int:id>", methods=["POST"])
@login_required
def delete_employee(id):
    conn = get_db_connection()
    emp = conn.execute("SELECT emp_id FROM employees WHERE id=?", (id,)).fetchone()
    if emp:
        emp_id = emp["emp_id"]
        conn.execute("DELETE FROM employees WHERE id=?", (id,))
        conn.execute("DELETE FROM attendance WHERE emp_id=?", (emp_id,))
        conn.commit()
        folder = os.path.join(FACES_DIR, emp_id)
        if os.path.exists(folder):
            shutil.rmtree(folder)
    conn.close()
    return jsonify({"success": True, "message": "Employee deleted successfully"})

@app.route("/register_face", methods=["POST"])
@login_required
def register_face():
    data = request.get_json()
    emp_id = data.get("employee_id")
    image_data = data.get("image")

    if not emp_id or not image_data:
        return jsonify({"success": False})

    folder = os.path.join(FACES_DIR, emp_id)
    os.makedirs(folder, exist_ok=True)

    img_path = os.path.join(folder, f"{emp_id}_1.png")
    img_bytes = base64.b64decode(image_data.split(",")[1])

    with open(img_path, "wb") as f:
        f.write(img_bytes)

    return jsonify({"success": True})

@app.route("/faces/<emp_id>/<filename>")
def serve_face(emp_id, filename):
    return send_from_directory(os.path.join(FACES_DIR, emp_id), filename)

# -------------------------
# Run App
# -------------------------
if __name__ == "__main__":
    os.makedirs(FACES_DIR, exist_ok=True)
    create_default_admin()
    create_tables()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

