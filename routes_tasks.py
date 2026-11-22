"""Task CRUD and 7 PM meeting routes — registered onto the main app."""
from __future__ import annotations
from datetime import date, datetime, timedelta
from flask import flash, redirect, render_template, request, session, url_for
import auth
import excel_utils as xl
import db_backup
from config import PRIORITY_OPTIONS, STATUS_OPTIONS


def register(app):
    @app.route("/task/add", methods=["GET", "POST"])
    @auth.login_required
    def add_task():
        if auth.is_manager():
            # managers can assign tasks to anyone — members AND other managers (including self)
            all_users = xl.get_all_users()
            members = [u["display_name"] or u["username"] for u in all_users if u.get("display_name") or u.get("username")]
        else:
            members = [session.get("team_name")]
        if request.method == "POST":
            raw_date  = request.form.get("task_date") or str(date.today())
            member    = request.form.get("team_member", "").strip()
            task      = request.form.get("task", "").strip()
            priority  = request.form.get("priority", "Medium")
            dll       = request.form.get("done_looks_like", "").strip()
            hrs_raw   = request.form.get("est_hours", "").strip()
            est_hours = float(hrs_raw) if hrs_raw else None
            if not auth.is_manager() and member != session.get("team_name"):
                flash("You can only add tasks for yourself.", "error")
                return redirect(url_for("add_task"))
            if not task or not member:
                flash("Task description and team member are required.", "error")
            else:
                d = datetime.strptime(raw_date, "%Y-%m-%d").date()
                if not auth.is_manager() and d < date.today():
                    flash("You cannot add tasks for past dates.", "error")
                    return redirect(url_for("add_task"))
                # Write to Excel (manager view) then immediately write to SQLite
                excel_row = xl.add_task(d, member, task, priority, dll, est_hours)
                db_backup.write_task_db(excel_row, d, member, task, priority,
                                        "Not started", dll, est_hours, "")
                db_backup.log_activity("add_task", session.get("username", ""),
                                       session.get("display_name", ""),
                                       f"Task for {member}: {task[:60]}")
                flash(f"Task added for {member}.", "success")
                return redirect(url_for("dashboard"))
        return render_template("add_task.html", members=members,
                               priorities=PRIORITY_OPTIONS, today=date.today(),
                               is_manager=auth.is_manager())

    @app.route("/task/edit/<int:row_num>", methods=["GET", "POST"])
    @auth.login_required
    def edit_task(row_num: int):
        tasks = db_backup.get_tasks_db()
        task = next((t for t in tasks if t["row"] == row_num), None)
        if not task:
            flash("Task not found.", "error")
            return redirect(url_for("dashboard"))
        if not auth.can_edit_task(task["team_member"]):
            flash("You can only edit your own tasks.", "error")
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            if auth.is_manager():
                t_text = request.form.get("task", task["task"])
                t_pri  = request.form.get("priority", task["priority"])
                t_stat = request.form.get("status", task["status"])
                t_dll  = request.form.get("done_looks_like", task["done_looks_like"] or "")
                t_hrs  = float(request.form.get("est_hours") or 0) or None
                t_7pm  = request.form.get("update_7pm", task["update_7pm"] or "")
                xl.update_task_full(row_num, t_text, t_pri, t_stat, t_dll, t_hrs, t_7pm)
                db_backup.write_task_db(row_num, task["date"], task["team_member"],
                                        t_text, t_pri, t_stat, t_dll, t_hrs, t_7pm)
            else:
                t_stat = request.form.get("status", task["status"])
                t_7pm  = request.form.get("update_7pm", task["update_7pm"] or "")
                xl.update_task_status(row_num, t_stat, t_7pm)
                db_backup.update_task_status_db(row_num, t_stat, t_7pm)
            db_backup.log_activity("update_task", session.get("username", ""),
                                   session.get("display_name", ""),
                                   f"Row {row_num}: {task.get('task','')[:60]}")
            flash("Task updated.", "success")
            return redirect(url_for("dashboard"))
        return render_template("edit_task.html", task=task,
                               statuses=STATUS_OPTIONS, priorities=PRIORITY_OPTIONS,
                               is_manager=auth.is_manager())

    @app.route("/task/status/<int:row_num>", methods=["POST"])
    @auth.login_required
    def quick_status(row_num: int):
        """Inline status update from the dashboard — no full edit page needed."""
        tasks = db_backup.get_tasks_db()
        task  = next((t for t in tasks if t["row"] == row_num), None)
        if not task or not auth.can_edit_task(task["team_member"]):
            flash("Not allowed.", "error")
            return redirect(url_for("dashboard"))
        new_status = request.form.get("status", "").strip()
        if new_status not in STATUS_OPTIONS:
            flash("Invalid status.", "error")
            return redirect(url_for("dashboard"))
        xl.update_task_status(row_num, new_status, task.get("update_7pm") or "")
        db_backup.update_task_status_db(row_num, new_status, task.get("update_7pm") or "")
        db_backup.log_activity("status_change", session.get("username", ""),
                               session.get("display_name", ""),
                               f"Row {row_num} → {new_status}")
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/task/delete/<int:row_num>", methods=["POST"])
    @auth.manager_required
    def delete_task(row_num: int):
        xl.delete_task(row_num)
        db_backup.delete_task_db(row_num)
        db_backup.log_activity("delete_task", session.get("username", ""),
                               session.get("display_name", ""), f"Row {row_num}")
        flash("Task deleted.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/meeting", methods=["GET", "POST"])
    @auth.login_required
    def meeting():
        if auth.is_manager():
            all_users = xl.get_all_users()
            members = [u["display_name"] or u["username"] for u in all_users if u.get("display_name") or u.get("username")]
        else:
            members = xl.get_team_members()
        tomorrow  = date.today() + timedelta(days=1)
        team_name = session.get("team_name", "")
        if request.method == "POST":
            allowed = members if auth.is_manager() else ([team_name] if team_name else [])
            added = 0
            for m in allowed:
                tasks_list = request.form.getlist(f"tasks_{m}")
                pris_list  = request.form.getlist(f"priorities_{m}")
                dlls_list  = request.form.getlist(f"dlls_{m}")
                for i, t in enumerate(tasks_list):
                    t = t.strip()
                    if t:
                        pri = pris_list[i] if i < len(pris_list) else "Medium"
                        dll = dlls_list[i].strip() if i < len(dlls_list) else ""
                        excel_row = xl.add_task(tomorrow, m, t, pri or "Medium", dll)
                        db_backup.write_task_db(excel_row, tomorrow, m, t,
                                                pri or "Medium", "Not started", dll, None, "")
                        db_backup.log_activity("add_task", session.get("username", ""),
                                               session.get("display_name", ""),
                                               f"7PM — Task for {m}: {t[:60]}")
                        added += 1
            msg = "Task added for tomorrow." if added == 1 else f"{added} tasks added for tomorrow."
            flash(msg, "success")
            return redirect(url_for("meeting"))
        grouped_next = {m: db_backup.get_tasks_db(filter_date=tomorrow, member=m) for m in members}
        return render_template("meeting.html", members=members, tomorrow=tomorrow,
                               grouped_next=grouped_next, priorities=PRIORITY_OPTIONS,
                               is_manager=auth.is_manager(), team_name=team_name)
