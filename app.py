from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, Response, jsonify
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from functools import wraps
import io
import os
import sys

try:
    from weasyprint import HTML
    HAS_WEASYPRINT = True
except Exception:
    HAS_WEASYPRINT = False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "kmlttb-reception-secret-2026")

DB_NAME = "reception.db"

ROLES = {
    "reception_3rd": {
        "label": "3rd Floor Reception Desk",
        "short_label": "3rd Floor Desk",
        "floor": "3rd Floor",
        "type": "desk",
        "pin": os.environ.get("PIN_3RD", "3333"),
        "icon": "bi-building-fill"
    },
    "reception_4th": {
        "label": "4th Floor Reception Desk",
        "short_label": "4th Floor Desk",
        "floor": "4th Floor",
        "type": "desk",
        "pin": os.environ.get("PIN_4TH", "4444"),
        "icon": "bi-layers-fill"
    },
    "registrar": {
        "label": "Registrar Executive Command",
        "short_label": "Registrar",
        "floor": "Executive",
        "type": "executive",
        "pin": os.environ.get("PIN_REGISTRAR", "1111"),
        "icon": "bi-award-fill"
    },
    "chairlady": {
        "label": "Chairlady Executive Command",
        "short_label": "Chairlady",
        "floor": "Executive",
        "type": "executive",
        "pin": os.environ.get("PIN_CHAIRLADY", "2222"),
        "icon": "bi-shield-check"
    }
}

DEPARTMENTS = [
    "Registry",
    "Laboratories",
    "Validation",
    "Administration",
    "Human Resource",
    "Indexing",
    "IT",
    "DCI",
    "Examination",
    "Procurement",
    "Inspectors",
    "Finance / Accounts",
    "Quality Assurance",
    "Legal & Compliance",
    "Public Relations / Customer Care",
    "Registrar's Office",
    "Chairlady's Office"
]

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reception_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            iso_date TEXT,
            date_logged TEXT NOT NULL,
            time_in TEXT NOT NULL,
            time_out TEXT,
            visitor_name TEXT NOT NULL,
            phone_no TEXT NOT NULL,
            national_id TEXT,
            visitor_category TEXT DEFAULT 'Practitioner / Applicant',
            purpose TEXT NOT NULL,
            file_or_doc_no TEXT,
            floor_location TEXT NOT NULL DEFAULT '3rd Floor',
            department_visited TEXT NOT NULL,
            staff_visited TEXT,
            receptionist_name TEXT NOT NULL,
            remarks TEXT,
            client_suggestion TEXT,
            satisfaction_rating TEXT,
            status TEXT DEFAULT 'Active Inside'
        )
    """)

    existing_cols = {row["name"] for row in cur.execute("PRAGMA table_info(reception_log)").fetchall()}
    migrations = {
        "iso_date": "ALTER TABLE reception_log ADD COLUMN iso_date TEXT",
        "visitor_category": "ALTER TABLE reception_log ADD COLUMN visitor_category TEXT DEFAULT 'Practitioner / Applicant'",
        "floor_location": "ALTER TABLE reception_log ADD COLUMN floor_location TEXT NOT NULL DEFAULT '3rd Floor'",
        "remarks": "ALTER TABLE reception_log ADD COLUMN remarks TEXT",
        "client_suggestion": "ALTER TABLE reception_log ADD COLUMN client_suggestion TEXT",
        "satisfaction_rating": "ALTER TABLE reception_log ADD COLUMN satisfaction_rating TEXT"
    }
    for col, stmt in migrations.items():
        if col not in existing_cols:
            try:
                cur.execute(stmt)
            except Exception:
                pass

    rows_no_iso = cur.execute("SELECT id, date_logged FROM reception_log WHERE iso_date IS NULL OR iso_date = ''").fetchall()
    for r in rows_no_iso:
        try:
            dt = datetime.strptime(r["date_logged"], "%d/%m/%Y")
            cur.execute("UPDATE reception_log SET iso_date = ? WHERE id = ?", (dt.strftime("%Y-%m-%d"), r["id"]))
        except Exception:
            cur.execute("UPDATE reception_log SET iso_date = ? WHERE id = ?", (datetime.now().strftime("%Y-%m-%d"), r["id"]))

    conn.commit()
    conn.close()

def reset_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS reception_log")
    conn.commit()
    conn.close()
    init_db()

init_db()

def login_required(allowed_roles=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            role = session.get("role")
            if not role or role not in ROLES:
                flash("Select a workspace and enter your 4-digit access PIN to continue.", "warning")
                return redirect(url_for("login"))
            if allowed_roles and role not in allowed_roles:
                flash("Your current role does not have access to that view.", "danger")
                return redirect(url_for("dashboard_router"))
            return f(*args, **kwargs)
        return wrapped
    return decorator

@app.context_processor
def inject_globals():
    role_key = session.get("role")
    return {
        "current_role": role_key,
        "role_info": ROLES.get(role_key),
        "all_roles": ROLES,
        "departments": DEPARTMENTS
    }

@app.route("/", methods=["GET"])
def index():
    if session.get("role") in ROLES:
        return redirect(url_for("dashboard_router"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        selected_role = request.form.get("role", "").strip()
        pin = request.form.get("pin", "").strip()
        duty_officer = request.form.get("duty_officer", "").strip().title()

        if selected_role not in ROLES:
            flash("Please select a valid dashboard role.", "danger")
            return redirect(url_for("login"))

        if pin != ROLES[selected_role]["pin"]:
            flash("Incorrect access PIN for the selected dashboard.", "danger")
            return redirect(url_for("login"))

        session["role"] = selected_role
        if duty_officer:
            session["duty_officer"] = duty_officer
        flash(f"Signed into {ROLES[selected_role]['label']}.", "success")
        return redirect(url_for("dashboard_router"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Signed out safely.", "info")
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required()
def dashboard_router():
    role = session.get("role")
    if role in ("reception_3rd", "reception_4th"):
        return redirect(url_for("desk_dashboard", floor_key=role))
    return redirect(url_for("executive_dashboard", exec_role=role))

# =====================================================================
# RETURNING VISITOR AUTOFILL API
# =====================================================================
@app.route("/api/visitor-lookup")
@login_required(["reception_3rd", "reception_4th"])
def visitor_lookup():
    q = request.args.get("q", "").strip()
    if len(q) < 3:
        return jsonify({"matches": []})

    conn = get_db()
    cur = conn.cursor()

    wildcard = f"%{q}%"
    rows = cur.execute("""
        SELECT
            id, visitor_name, phone_no, national_id, visitor_category,
            file_or_doc_no, department_visited, staff_visited, purpose,
            date_logged, floor_location
        FROM reception_log
        WHERE phone_no LIKE ?
           OR national_id LIKE ?
           OR file_or_doc_no LIKE ?
           OR visitor_name LIKE ?
        ORDER BY id DESC
        LIMIT 25
    """, (wildcard, wildcard, wildcard, wildcard)).fetchall()

    seen = set()
    matches = []
    for r in rows:
        key = (r["phone_no"].strip(), r["visitor_name"].strip().lower())
        if key in seen:
            continue
        seen.add(key)

        visit_count = cur.execute(
            "SELECT COUNT(*) FROM reception_log WHERE phone_no = ? AND LOWER(visitor_name) = ?",
            (r["phone_no"], r["visitor_name"].lower())
        ).fetchone()[0]

        matches.append({
            "visitor_name": r["visitor_name"] or "",
            "phone_no": r["phone_no"] or "",
            "national_id": r["national_id"] or "",
            "visitor_category": r["visitor_category"] or "Practitioner / Applicant",
            "file_or_doc_no": r["file_or_doc_no"] or "",
            "department_visited": r["department_visited"] or "",
            "staff_visited": r["staff_visited"] or "",
            "last_purpose": r["purpose"] or "",
            "last_visit_date": r["date_logged"] or "",
            "last_floor": r["floor_location"] or "",
            "visit_count": visit_count
        })
        if len(matches) >= 6:
            break

    conn.close()
    return jsonify({"matches": matches})

# =====================================================================
# LIVE EXECUTIVE RECOMMENDATIONS FEED API (3RD + 4TH FLOOR -> EXEC)
# =====================================================================
@app.route("/api/executive-recommendations")
@login_required(["registrar", "chairlady"])
def api_executive_recommendations():
    period = request.args.get("period", "today")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    floor_filter = request.args.get("floor", "ALL")
    dept_filter = request.args.get("department", "ALL")

    start_iso, end_iso, _ = resolve_date_range(period, start_date, end_date)
    analytics = fetch_executive_analytics(start_iso, end_iso, floor_filter, dept_filter)
    return jsonify({
        "suggestions_count": analytics["suggestions_count"],
        "suggestions": analytics["suggestions"]
    })

# =====================================================================
# RECEPTION DESK DASHBOARDS (3RD FLOOR & 4TH FLOOR)
# =====================================================================
@app.route("/desk/<floor_key>", methods=["GET", "POST"])
@login_required(["reception_3rd", "reception_4th"])
def desk_dashboard(floor_key):
    if floor_key not in ("reception_3rd", "reception_4th"):
        return redirect(url_for("dashboard_router"))

    if session.get("role") != floor_key:
        flash("Access restricted to your signed-in reception floor.", "warning")
        return redirect(url_for("desk_dashboard", floor_key=session.get("role")))

    floor_name = ROLES[floor_key]["floor"]
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        now = datetime.now()
        receptionist_name = request.form.get("receptionist_name", "").strip().title()
        if receptionist_name:
            session["duty_officer"] = receptionist_name

        dept_selected = request.form.get("department_visited", "").strip()
        if dept_selected == "Other":
            custom_dept = request.form.get("custom_department", "").strip().title()
            dept_selected = custom_dept if custom_dept else "General / Other"

        suggestion_text = request.form.get("client_suggestion", "").strip()

        cur.execute("""
            INSERT INTO reception_log (
                iso_date, date_logged, time_in, time_out, visitor_name, phone_no, national_id,
                visitor_category, purpose, file_or_doc_no, floor_location,
                department_visited, staff_visited, receptionist_name,
                remarks, client_suggestion, satisfaction_rating, status
            )
            VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active Inside')
        """, (
            now.strftime("%Y-%m-%d"),
            now.strftime("%d/%m/%Y"),
            now.strftime("%I:%M %p"),
            request.form.get("visitor_name", "").strip().title(),
            request.form.get("phone_no", "").strip(),
            request.form.get("national_id", "").strip().upper(),
            request.form.get("visitor_category", "Practitioner / Applicant").strip(),
            request.form.get("purpose", "").strip(),
            request.form.get("file_or_doc_no", "").strip().upper(),
            floor_name,
            dept_selected,
            request.form.get("staff_visited", "").strip().title(),
            receptionist_name,
            request.form.get("remarks", "").strip(),
            suggestion_text,
            request.form.get("satisfaction_rating", "").strip()
        ))
        conn.commit()
        if suggestion_text:
            flash(f"Checked in at {floor_name} & shared client recommendation with Registrar and Chairlady.", "success")
        else:
            flash(f"Visitor checked in at {floor_name} Reception Desk for {dept_selected}.", "success")
        return redirect(url_for("desk_dashboard", floor_key=floor_key))

    search_query = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()
    dept_filter = request.args.get("department", "").strip()
    scope_filter = request.args.get("scope", "my_floor").strip()

    sql = "SELECT * FROM reception_log WHERE 1=1"
    params = []

    if scope_filter == "my_floor":
        sql += " AND floor_location = ?"
        params.append(floor_name)

    if search_query:
        sql += """ AND (
            visitor_name LIKE ? OR phone_no LIKE ? OR national_id LIKE ?
            OR file_or_doc_no LIKE ? OR department_visited LIKE ? OR remarks LIKE ? OR client_suggestion LIKE ?
        )"""
        wildcard = f"%{search_query}%"
        params.extend([wildcard] * 7)

    if dept_filter:
        sql += " AND department_visited = ?"
        params.append(dept_filter)

    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter)

    sql += " ORDER BY id DESC LIMIT 120"
    entries = cur.execute(sql, params).fetchall()

    today_iso = datetime.now().strftime("%Y-%m-%d")

    floor_active = cur.execute(
        "SELECT COUNT(*) FROM reception_log WHERE floor_location = ? AND status = 'Active Inside'",
        (floor_name,)
    ).fetchone()[0]

    floor_today = cur.execute(
        "SELECT COUNT(*) FROM reception_log WHERE floor_location = ? AND iso_date = ?",
        (floor_name, today_iso)
    ).fetchone()[0]

    building_active = cur.execute(
        "SELECT COUNT(*) FROM reception_log WHERE status = 'Active Inside'"
    ).fetchone()[0]

    floor_departed_today = cur.execute(
        "SELECT COUNT(*) FROM reception_log WHERE floor_location = ? AND iso_date = ? AND status = 'Departed'",
        (floor_name, today_iso)
    ).fetchone()[0]

    shared_suggestions_today = cur.execute(
        """SELECT id, time_in, visitor_name, floor_location, department_visited, receptionist_name,
                  client_suggestion, satisfaction_rating
           FROM reception_log
           WHERE iso_date = ? AND client_suggestion IS NOT NULL AND TRIM(client_suggestion) != ''
           ORDER BY id DESC LIMIT 12""",
        (today_iso,)
    ).fetchall()

    conn.close()
    return render_template(
        "desk_dashboard.html",
        floor_key=floor_key,
        floor_name=floor_name,
        entries=entries,
        floor_active=floor_active,
        floor_today=floor_today,
        building_active=building_active,
        floor_departed_today=floor_departed_today,
        shared_suggestions_today=[dict(s) for s in shared_suggestions_today],
        search_query=search_query,
        status_filter=status_filter,
        dept_filter=dept_filter,
        scope_filter=scope_filter,
        saved_officer=session.get("duty_officer", "")
    )

@app.route("/checkout/<int:entry_id>", methods=["POST"])
@login_required(["reception_3rd", "reception_4th"])
def checkout(entry_id):
    time_out = datetime.now().strftime("%I:%M %p")
    checkout_remarks = request.form.get("checkout_remarks", "").strip()
    client_suggestion = request.form.get("client_suggestion", "").strip()
    satisfaction_rating = request.form.get("satisfaction_rating", "").strip()

    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("SELECT remarks, client_suggestion, satisfaction_rating FROM reception_log WHERE id = ?", (entry_id,)).fetchone()

    if row:
        new_remarks = row["remarks"] or ""
        if checkout_remarks:
            new_remarks = f"{new_remarks} | Exit Note: {checkout_remarks}" if new_remarks else checkout_remarks

        new_suggestion = row["client_suggestion"] or ""
        if client_suggestion:
            new_suggestion = f"{new_suggestion} | {client_suggestion}" if new_suggestion else client_suggestion

        new_rating = satisfaction_rating if satisfaction_rating else (row["satisfaction_rating"] or "")

        cur.execute("""
            UPDATE reception_log
            SET time_out = ?,
                status = 'Departed',
                remarks = ?,
                client_suggestion = ?,
                satisfaction_rating = ?
            WHERE id = ?
        """, (time_out, new_remarks, new_suggestion, new_rating, entry_id))
        conn.commit()
        if client_suggestion:
            flash(f"Signed out at {time_out} & synced client recommendation to Registrar & Chairlady.", "info")
        else:
            flash(f"Visitor signed out at {time_out}.", "info")

    conn.close()
    return redirect(url_for("desk_dashboard", floor_key=session.get("role")))

@app.route("/transfer/<int:entry_id>", methods=["POST"])
@login_required(["reception_3rd", "reception_4th"])
def transfer_floor(entry_id):
    target_floor = request.form.get("target_floor", "").strip()
    target_dept = request.form.get("target_department", "").strip()
    note = request.form.get("transfer_note", "").strip()

    if target_floor not in ("3rd Floor", "4th Floor"):
        flash("Invalid target floor.", "danger")
        return redirect(url_for("desk_dashboard", floor_key=session.get("role")))

    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("SELECT remarks, department_visited FROM reception_log WHERE id = ?", (entry_id,)).fetchone()
    if row:
        dept = target_dept if target_dept else row["department_visited"]
        stamp = datetime.now().strftime("%I:%M %p")
        transfer_msg = f"Transferred to {target_floor} ({dept}) at {stamp}"
        if note:
            transfer_msg += f" - {note}"
        updated_remarks = f"{row['remarks']} | {transfer_msg}" if row["remarks"] else transfer_msg

        cur.execute("""
            UPDATE reception_log
            SET floor_location = ?, department_visited = ?, remarks = ?
            WHERE id = ?
        """, (target_floor, dept, updated_remarks, entry_id))
        conn.commit()
        flash(f"Visitor transferred to {target_floor} ({dept}).", "success")

    conn.close()
    return redirect(url_for("desk_dashboard", floor_key=session.get("role")))

def resolve_date_range(period, start_date_str=None, end_date_str=None):
    today = datetime.now().date()
    period = (period or "today").lower()

    if period == "today":
        start_d = today
        end_d = today
        label = f"Daily Report ({today.strftime('%d %b %Y')})"
    elif period == "weekly":
        start_d = today - timedelta(days=today.weekday())
        end_d = today
        label = f"Weekly Report ({start_d.strftime('%d %b %Y')} - {end_d.strftime('%d %b %Y')})"
    elif period == "monthly":
        start_d = today.replace(day=1)
        end_d = today
        label = f"Monthly Report ({today.strftime('%B %Y')})"
    elif period == "yearly":
        start_d = today.replace(month=1, day=1)
        end_d = today
        label = f"Yearly Report ({today.year})"
    elif period == "custom" and start_date_str and end_date_str:
        try:
            start_d = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_d = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if start_d > end_d:
                start_d, end_d = end_d, start_d
            label = f"Custom Period ({start_d.strftime('%d %b %Y')} - {end_d.strftime('%d %b %Y')})"
        except ValueError:
            start_d = today
            end_d = today
            label = f"Daily Report ({today.strftime('%d %b %Y')})"
    elif period == "all":
        start_d = datetime(2000, 1, 1).date()
        end_d = today
        label = "All-Time Cumulative Report"
    else:
        start_d = today
        end_d = today
        label = f"Daily Report ({today.strftime('%d %b %Y')})"

    return start_d.strftime("%Y-%m-%d"), end_d.strftime("%Y-%m-%d"), label

def fetch_executive_analytics(start_iso, end_iso, floor_filter="ALL", dept_filter="ALL"):
    conn = get_db()
    cur = conn.cursor()

    where_clause = "WHERE iso_date BETWEEN ? AND ?"
    params = [start_iso, end_iso]

    if floor_filter in ("3rd Floor", "4th Floor"):
        where_clause += " AND floor_location = ?"
        params.append(floor_filter)

    if dept_filter and dept_filter != "ALL":
        where_clause += " AND department_visited = ?"
        params.append(dept_filter)

    total_visitors = cur.execute(f"SELECT COUNT(*) FROM reception_log {where_clause}", params).fetchone()[0]
    visitors_in = cur.execute(f"SELECT COUNT(*) FROM reception_log {where_clause} AND status = 'Active Inside'", params).fetchone()[0]
    visitors_out = cur.execute(f"SELECT COUNT(*) FROM reception_log {where_clause} AND status = 'Departed'", params).fetchone()[0]

    third_floor_count = cur.execute(
        "SELECT COUNT(*) FROM reception_log WHERE iso_date BETWEEN ? AND ? AND floor_location = '3rd Floor'",
        (start_iso, end_iso)
    ).fetchone()[0]
    fourth_floor_count = cur.execute(
        "SELECT COUNT(*) FROM reception_log WHERE iso_date BETWEEN ? AND ? AND floor_location = '4th Floor'",
        (start_iso, end_iso)
    ).fetchone()[0]

    suggestions_count = cur.execute(
        f"SELECT COUNT(*) FROM reception_log {where_clause} AND client_suggestion IS NOT NULL AND TRIM(client_suggestion) != ''",
        params
    ).fetchone()[0]

    dept_rows = cur.execute(
        f"SELECT department_visited, COUNT(*) as cnt FROM reception_log {where_clause} GROUP BY department_visited ORDER BY cnt DESC",
        params
    ).fetchall()

    cat_rows = cur.execute(
        f"SELECT COALESCE(NULLIF(visitor_category, ''), 'General Visitor') as cat, COUNT(*) as cnt FROM reception_log {where_clause} GROUP BY cat ORDER BY cnt DESC",
        params
    ).fetchall()

    trend_rows = cur.execute(
        f"SELECT iso_date, date_logged, COUNT(*) as cnt FROM reception_log {where_clause} GROUP BY iso_date ORDER BY iso_date ASC LIMIT 31",
        params
    ).fetchall()

    rating_rows = cur.execute(
        f"SELECT satisfaction_rating, COUNT(*) as cnt FROM reception_log {where_clause} AND satisfaction_rating IS NOT NULL AND TRIM(satisfaction_rating) != '' GROUP BY satisfaction_rating",
        params
    ).fetchall()

    suggestions = cur.execute(
        f"""SELECT id, date_logged, time_in, visitor_name, phone_no, floor_location,
                  department_visited, receptionist_name, client_suggestion, satisfaction_rating
           FROM reception_log {where_clause}
           AND client_suggestion IS NOT NULL AND TRIM(client_suggestion) != ''
           ORDER BY id DESC LIMIT 80""",
        params
    ).fetchall()

    records = cur.execute(
        f"SELECT * FROM reception_log {where_clause} ORDER BY id DESC LIMIT 300",
        params
    ).fetchall()

    conn.close()
    return {
        "total_visitors": total_visitors,
        "visitors_in": visitors_in,
        "visitors_out": visitors_out,
        "third_floor_count": third_floor_count,
        "fourth_floor_count": fourth_floor_count,
        "suggestions_count": suggestions_count,
        "dept_rows": [dict(r) for r in dept_rows],
        "cat_rows": [dict(r) for r in cat_rows],
        "trend_rows": [dict(r) for r in trend_rows],
        "rating_rows": [dict(r) for r in rating_rows],
        "suggestions": [dict(r) for r in suggestions],
        "records": [dict(r) for r in records]
    }

@app.route("/executive/<exec_role>")
@login_required(["registrar", "chairlady"])
def executive_dashboard(exec_role):
    if exec_role not in ("registrar", "chairlady"):
        return redirect(url_for("dashboard_router"))

    if session.get("role") != exec_role:
        return redirect(url_for("executive_dashboard", exec_role=session.get("role")))

    period = request.args.get("period", "today")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    floor_filter = request.args.get("floor", "ALL")
    dept_filter = request.args.get("department", "ALL")

    start_iso, end_iso, period_label = resolve_date_range(period, start_date, end_date)
    analytics = fetch_executive_analytics(start_iso, end_iso, floor_filter, dept_filter)

    return render_template(
        "executive_dashboard.html",
        exec_role=exec_role,
        exec_title=ROLES[exec_role]["label"],
        period=period,
        start_date=start_iso,
        end_date=end_iso,
        floor_filter=floor_filter,
        dept_filter=dept_filter,
        period_label=period_label,
        analytics=analytics
    )

@app.route("/report/pdf")
@login_required(["registrar", "chairlady", "reception_3rd", "reception_4th"])
def generate_pdf_report():
    period = request.args.get("period", "today")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    floor_filter = request.args.get("floor", "ALL")
    dept_filter = request.args.get("department", "ALL")

    role = session.get("role")
    if role == "reception_3rd" and floor_filter == "ALL":
        floor_filter = "3rd Floor"
    elif role == "reception_4th" and floor_filter == "ALL":
        floor_filter = "4th Floor"

    start_iso, end_iso, period_label = resolve_date_range(period, start_date, end_date)
    analytics = fetch_executive_analytics(start_iso, end_iso, floor_filter, dept_filter)

    generated_by = ROLES[role]["label"]
    generated_at = datetime.now().strftime("%d %B %Y at %I:%M %p")

    html_content = render_template(
        "report_pdf.html",
        period_label=period_label,
        floor_filter=floor_filter,
        dept_filter=dept_filter,
        analytics=analytics,
        generated_by=generated_by,
        generated_at=generated_at
    )

    if HAS_WEASYPRINT:
        pdf_bytes = HTML(string=html_content).write_pdf()
        filename = f"KMLTTB_Reception_{period.capitalize()}_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename
        )
    else:
        return Response(html_content, mimetype="text/html")

@app.route("/export/excel")
@login_required()
def export_excel():
    period = request.args.get("period", "all")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    floor_filter = request.args.get("floor", "ALL")
    dept_filter = request.args.get("department", "ALL")

    start_iso, end_iso, _ = resolve_date_range(period, start_date, end_date)

    query = """
        SELECT
            id AS "ENTRY ID",
            date_logged AS "DATE",
            floor_location AS "FLOOR DESK",
            time_in AS "TIME IN",
            COALESCE(time_out, 'Still Inside') AS "TIME OUT",
            visitor_name AS "VISITOR NAME",
            phone_no AS "PHONE NUMBER",
            national_id AS "ID / PASSPORT",
            visitor_category AS "CATEGORY",
            purpose AS "PURPOSE",
            file_or_doc_no AS "REGISTRATION NUMBER",
            department_visited AS "DEPARTMENT VISITING",
            staff_visited AS "STAFF VISITED",
            receptionist_name AS "DUTY RECEPTIONIST",
            remarks AS "REMARKS / EXIT NOTES",
            client_suggestion AS "CLIENT RECOMMENDATION / FEEDBACK",
            satisfaction_rating AS "EXPERIENCE RATING",
            status AS "STATUS"
        FROM reception_log
        WHERE iso_date BETWEEN ? AND ?
    """
    params = [start_iso, end_iso]
    if floor_filter in ("3rd Floor", "4th Floor"):
        query += " AND floor_location = ?"
        params.append(floor_filter)
    if dept_filter and dept_filter != "ALL":
        query += " AND department_visited = ?"
        params.append(dept_filter)
    query += " ORDER BY id DESC"

    conn = get_db()
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Reception_Log")
    output.seek(0)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    return send_file(
        output,
        as_attachment=True,
        download_name=f"KMLTTB_Reception_Log_{timestamp}.xlsx"
    )

if __name__ == "__main__":
    if "--reset-db" in sys.argv:
        reset_db()
        print("reception.db has been reset cleanly.")
        sys.exit(0)
    port = int(os.environ.get("PORT", 5001))
    debug_mode = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
