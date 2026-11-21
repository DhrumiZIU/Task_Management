"""Task Management Web App — Flask entry point."""
from __future__ import annotations
import json, os, subprocess, threading, time
from datetime import date, timedelta
from flask import Flask, flash, redirect, render_template, request, session, url_for
import auth
import db_backup

app = Flask(__name__)
# SECRET_KEY from environment variable on Render; fallback for local dev only
app.secret_key = os.environ.get("SECRET_KEY", "local-dev-only-change-on-render")

# Startup: init SQLite, seed default users, sync from Excel if available
with app.app_context():
    db_backup.init_db()
    db_backup.seed_users()          # no-op if users already exist
    try:
        import excel_utils as xl
        db_backup.sync_from_excel()
    except Exception:
        pass                        # Excel not present on Render — that's fine


def _excel_watcher():
    """Background thread: auto-sync SQLite when manager saves the Excel file."""
    last_mtime = db_backup._excel_mtime()
    pending    = False   # mtime changed, waiting for Excel to finish writing
    changed_at = 0.0
    DEBOUNCE   = 3.0     # seconds to wait after last change before syncing

    while True:
        time.sleep(2)
        try:
            current = db_backup._excel_mtime()
            if current != last_mtime:
                last_mtime = current
                pending    = True
                changed_at = time.time()
            if pending and (time.time() - changed_at) >= DEBOUNCE:
                pending = False
                db_backup.sync_from_excel()   # Excel fully written — safe to sync
        except Exception:
            pass


# Start watcher only once (not inside Flask's reloader child process)
_watcher = threading.Thread(target=_excel_watcher, daemon=True)
_watcher.start()

# Register route modules
import routes_tasks; routes_tasks.register(app)
import routes_admin; routes_admin.register(app)


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    if session.get("username"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if auth.login_user(username, password):
            db_backup.log_activity("login", session.get("username", username),
                                   session.get("display_name", username))
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    db_backup.log_activity("logout", session.get("username", ""),
                           session.get("display_name", ""))
    auth.logout_user()
    return redirect(url_for("login"))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@auth.login_required
def dashboard():
    today    = date.today()
    tomorrow = today + timedelta(days=1)
    view = request.args.get("view", "today") if auth.is_manager() else "today"
    if view == "tomorrow":
        filter_date = tomorrow
    elif view == "all":
        filter_date = None
    else:
        view, filter_date = "today", today

    # Read from SQLite — safe even when Excel is open/being edited by manager
    tasks   = db_backup.get_tasks_db(filter_date=filter_date)
    try:
        import excel_utils as xl
        members = xl.get_team_members()
    except Exception:
        members = [u["team_name"] for u in db_backup.get_users_backup()
                   if u.get("role") == "member" and u.get("team_name")]
    grouped = {m: [] for m in members}
    for t in tasks:
        if t["team_member"] in grouped:
            grouped[t["team_member"]].append(t)
    return render_template(
        "dashboard.html", grouped=grouped, members=members,
        today=today, tomorrow=tomorrow, current_view=view,
        is_manager=auth.is_manager(), can_edit_task=auth.can_edit_task,
        can_delete=auth.can_delete_task(), team_name=session.get("team_name", ""),
    )


# ── Analytics — always all tasks, matches dashboard "All Dates" ───────────────

@app.route("/analytics")
@auth.login_required
def analytics():
    tasks   = db_backup.get_tasks_db()
    try:
        import excel_utils as xl
        members = xl.get_team_members()
    except Exception:
        members = [u["team_name"] for u in db_backup.get_users_backup()
                   if u.get("role") == "member" and u.get("team_name")]
    sc = {"Done": 0, "In progress": 0, "Not started": 0, "Blocked": 0}
    pc = {"High": 0, "Medium": 0, "Low": 0}
    md = {m: {"total": 0, "done": 0} for m in members}
    for t in tasks:
        s = t.get("status") or "Not started"
        p = t.get("priority") or "Medium"
        m = t.get("team_member")
        if s in sc: sc[s] += 1
        if p in pc: pc[p] += 1
        if m in md:
            md[m]["total"] += 1
            if s == "Done": md[m]["done"] += 1
    done_pct = int(sc["Done"] / len(tasks) * 100) if tasks else 0
    return render_template("analytics.html",
        total_tasks=len(tasks), done_count=sc["Done"],
        inprog_count=sc["In progress"], blocked_count=sc["Blocked"], done_pct=done_pct,
        status_labels=json.dumps(list(sc.keys())),   status_data=json.dumps(list(sc.values())),
        priority_labels=json.dumps(list(pc.keys())), priority_data=json.dumps(list(pc.values())),
        member_labels=json.dumps(members),
        member_total=json.dumps([md[m]["total"] for m in members]),
        member_done=json.dumps([md[m]["done"]  for m in members]),
    )


if __name__ == "__main__":
    subprocess.run("lsof -ti:5001 | xargs kill -9 2>/dev/null", shell=True)
    app.run(debug=True, port=5001, use_reloader=False, threaded=True)
