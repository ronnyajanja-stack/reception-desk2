from flask import Flask, render_template, request, redirect, url_for, flash, send_file
import sqlite3
import pandas as pd
from datetime import datetime
import io
import os

app = Flask(__name__)
# Secret key: reads from environment variable if set, otherwise generates a secure token
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

DB_NAME = "reception.db"

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
            date_logged TEXT NOT NULL,
            time_in TEXT NOT NULL,
            time_out TEXT,
            visitor_name TEXT NOT NULL,
            phone_no TEXT NOT NULL,
            national_id TEXT,
            purpose TEXT NOT NULL,
            file_or_doc_no TEXT,
            department_visited TEXT NOT NULL,
            staff_visited TEXT,
            receptionist_name TEXT NOT NULL,
            remarks TEXT,
            status TEXT DEFAULT 'Active Inside'
        )
    """)
    # Add remarks column if upgrading an existing database
    try:
        cur.execute("ALTER TABLE reception_log ADD COLUMN remarks TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()

init_db()

@app.route("/", methods=["GET", "POST"])
def reception_portal():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        now = datetime.now()
        cur.execute("""
            INSERT INTO reception_log (
                date_logged, time_in, time_out, visitor_name, phone_no, national_id, purpose,
                file_or_doc_no, department_visited, staff_visited, receptionist_name, remarks, status
            )
            VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active Inside')
        """, (
            now.strftime("%d/%m/%Y"),
            now.strftime("%I:%M %p"),
            request.form.get("visitor_name", "").strip().title(),
            request.form.get("phone_no", "").strip(),
            request.form.get("national_id", "").strip(),
            request.form.get("purpose", "").strip(),
            request.form.get("file_or_doc_no", "").strip().upper(),
            request.form.get("department_visited", "").strip(),
            request.form.get("staff_visited", "").strip().title(),
            request.form.get("receptionist_name", "").strip().title(),
            request.form.get("remarks", "").strip()
        ))
        conn.commit()
        flash("Visitor successfully checked in!", "success")
        return redirect(url_for("reception_portal"))

    search_query = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()

    sql = "SELECT * FROM reception_log WHERE 1=1"
    params = []

    if search_query:
        sql += " AND (visitor_name LIKE ? OR phone_no LIKE ? OR national_id LIKE ? OR file_or_doc_no LIKE ? OR remarks LIKE ?)"
        wildcard = f"%{search_query}%"
        params.extend([wildcard, wildcard, wildcard, wildcard, wildcard])

    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter)

    sql += " ORDER BY id DESC LIMIT 100"
    entries = cur.execute(sql, params).fetchall()

    active_count = cur.execute("SELECT COUNT(*) FROM reception_log WHERE status = 'Active Inside'").fetchone()[0]
    total_today = cur.execute("SELECT COUNT(*) FROM reception_log WHERE date_logged = ?", (datetime.now().strftime("%d/%m/%Y"),)).fetchone()[0]

    conn.close()
    return render_template(
        "reception.html",
        entries=entries,
        active_count=active_count,
        total_today=total_today,
        search_query=search_query,
        status_filter=status_filter
    )

@app.route("/checkout/<int:entry_id>", methods=["POST"])
def checkout(entry_id):
    time_out = datetime.now().strftime("%I:%M %p")
    checkout_remarks = request.form.get("checkout_remarks", "").strip()

    conn = get_db()
    cur = conn.cursor()

    if checkout_remarks:
        cur.execute("""
            UPDATE reception_log 
            SET time_out = ?, 
                status = 'Departed',
                remarks = CASE 
                    WHEN remarks IS NULL OR remarks = '' THEN ? 
                    ELSE remarks || ' | Exit Note: ' || ? 
                END
            WHERE id = ?
        """, (time_out, checkout_remarks, checkout_remarks, entry_id))
    else:
        cur.execute("""
            UPDATE reception_log 
            SET time_out = ?, status = 'Departed' 
            WHERE id = ?
        """, (time_out, entry_id))

    conn.commit()
    conn.close()
    flash(f"Departure recorded at {time_out}.", "info")
    return redirect(url_for("reception_portal"))

@app.route("/export")
def export_excel():
    conn = get_db()
    df = pd.read_sql_query("""
        SELECT 
            id AS "ENTRY ID",
            date_logged AS "DATE",
            time_in AS "TIME IN",
            time_out AS "TIME OUT",
            visitor_name AS "VISITOR NAME",
            phone_no AS "PHONE NUMBER",
            national_id AS "ID / PASSPORT",
            purpose AS "PURPOSE",
            file_or_doc_no AS "REGISTRATION NUMBER",
            department_visited AS "DEPARTMENT VISITING",
            staff_visited AS "STAFF VISITED",
            receptionist_name AS "DUTY RECEPTIONIST",
            remarks AS "COMMENTS / RECOMMENDATIONS",
            status AS "STATUS"
        FROM reception_log 
        ORDER BY id DESC
    """, conn)
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Reception_Daily_Log")
    output.seek(0)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    return send_file(
        output,
        as_attachment=True,
        download_name=f"KMLTTB_Reception_Log_{timestamp}.xlsx"
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    debug_mode = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)