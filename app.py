"""Task Management Web App — Flask entry point."""
from __future__ import annotations
import json, os
from datetime import date, timedelta
from dotenv import load_dotenv
# override=False keeps DATABASE_URL from shell env (empty = SQLite locally)
load_dotenv(override=False)
from flask import Flask, flash, redirect, render_template, request, session, url_for
import auth
import db_backup

app = Flask(__name__)
# SECRET_KEY from environment variable on Render; fallback for local dev only
app.secret_key = os.environ.get("SECRET_KEY", "local-dev-only-change-on-render")
# Secure session cookies: HttpOnly prevents JS access, Lax blocks CSRF
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

_db_ready = False

def _ensure_db():
    global _db_ready
    if not _db_ready:
        db_backup.init_db()
        db_backup.seed_users()
        _db_ready = True

@app.before_request
def before_request():
    _ensure_db()

@app.after_request
def add_no_cache(response):
    """Prevent Vercel/CDN from caching authenticated pages."""
    if "username" in session or request.endpoint in ("login", "logout"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

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
        all_users = xl.get_all_users()
    except Exception:
        all_users = db_backup.get_users_backup()

    members       = [u["team_name"]    for u in all_users if u.get("role") == "member"  and u.get("team_name")]
    manager_names = [u["display_name"] for u in all_users if u.get("role") == "manager" and u.get("display_name")]

    # Build grouped for members + managers so manager tasks are tracked
    all_names = members + [n for n in manager_names if n not in members]
    grouped = {m: [] for m in all_names}
    for t in tasks:
        if t["team_member"] in grouped:
            grouped[t["team_member"]].append(t)
        elif t["team_member"]:
            # Task assigned to someone not in the list yet — still show it to managers
            grouped[t["team_member"]] = [t]
            if auth.is_manager() and t["team_member"] not in all_names:
                all_names.append(t["team_member"])

    history_tasks = db_backup.get_history_tasks(days=14)

    # Add-task drawer: always show all team members so anyone can assign to anyone
    drawer_members = [u.get("display_name") or u.get("username") for u in all_users
                      if u.get("display_name") or u.get("username")]

    return render_template(
        "dashboard.html", grouped=grouped, members=members,
        manager_names=manager_names,
        today=today, tomorrow=tomorrow, current_view=view,
        is_manager=auth.is_manager(), can_edit_task=auth.can_edit_task,
        can_delete=auth.can_delete_task(), team_name=session.get("team_name", ""),
        history_tasks=history_tasks, drawer_members=drawer_members,
        priorities=["High", "Medium", "Low"],
    )


# ── Analytics — always all tasks, matches dashboard "All Dates" ───────────────

@app.route("/analytics")
@auth.login_required
def analytics():
    from datetime import date, timedelta
    is_mgr = auth.is_manager()

    # Manager gets a view tab (today/tomorrow/all); members always see today only
    if is_mgr:
        current_view = request.args.get("view", "today")
        if current_view not in ("today", "tomorrow", "all"):
            current_view = "today"
    else:
        current_view = "today"

    today    = date.today()
    tomorrow = today + timedelta(days=1)

    if current_view == "today":
        tasks = db_backup.get_tasks_db(filter_date=today)
    elif current_view == "tomorrow":
        tasks = db_backup.get_tasks_db(filter_date=tomorrow)
    else:
        tasks = db_backup.get_tasks_db()

    try:
        import excel_utils as xl
        members = xl.get_team_members()
    except Exception:
        members = [u["team_name"] for u in db_backup.get_users_backup()
                   if u.get("role") == "member" and u.get("team_name")]

    sc = {"Done": 0, "In progress": 0, "Not started": 0, "Blocked": 0}
    pc = {"High": 0, "Medium": 0, "Low": 0}
    md = {m: {"total": 0, "done": 0, "inprog": 0, "blocked": 0, "not_started": 0} for m in members}
    for t in tasks:
        s = t.get("status") or "Not started"
        p = t.get("priority") or "Medium"
        m = t.get("team_member")
        if s in sc: sc[s] += 1
        if p in pc: pc[p] += 1
        if m in md:
            md[m]["total"] += 1
            if s == "Done":          md[m]["done"] += 1
            elif s == "In progress": md[m]["inprog"] += 1
            elif s == "Blocked":     md[m]["blocked"] += 1
            else:                    md[m]["not_started"] += 1

    done_pct    = int(sc["Done"] / len(tasks) * 100) if tasks else 0
    not_started = sc.get("Not started", 0)
    member_pct  = [int(md[m]["done"] / md[m]["total"] * 100) if md[m]["total"] > 0 else 0 for m in members]

    return render_template("analytics.html",
        total_tasks=len(tasks), done_count=sc["Done"],
        inprog_count=sc["In progress"], blocked_count=sc["Blocked"],
        not_started=not_started, done_pct=done_pct,
        status_labels=json.dumps(list(sc.keys())),   status_data=json.dumps(list(sc.values())),
        priority_labels=json.dumps(list(pc.keys())), priority_data=json.dumps(list(pc.values())),
        member_labels=json.dumps(members),
        member_total=json.dumps([md[m]["total"]   for m in members]),
        member_done=json.dumps([md[m]["done"]      for m in members]),
        member_inprog=json.dumps([md[m]["inprog"]  for m in members]),
        member_blocked=json.dumps([md[m]["blocked"] for m in members]),
        member_pct=json.dumps(member_pct),
        members_data=members, md=md,
        is_manager=is_mgr, current_view=current_view,
        today=today, tomorrow=tomorrow,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001, use_reloader=False, threaded=True)
