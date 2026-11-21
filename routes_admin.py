"""Users, projects, discussion and backup routes — registered onto the main app."""
from __future__ import annotations
from flask import flash, redirect, render_template, request, session, url_for
import auth
import excel_utils as xl
import db_backup
from config import ROLE_OPTIONS


def register(app):

    # ── Users ──────────────────────────────────────────────────────────────

    @app.route("/users")
    @auth.manager_required
    def users():
        return render_template("users.html", users=xl.get_all_users(), roles=ROLE_OPTIONS,
                               current_username=session.get("username"))

    @app.route("/users/add", methods=["POST"])
    @auth.manager_required
    def add_user():
        username     = request.form.get("username", "").strip().lower()
        password     = request.form.get("password", "").strip()
        role         = request.form.get("role", "member")
        display_name = request.form.get("display_name", "").strip()
        team_name    = request.form.get("team_name", "").strip()
        if not username or not password or not display_name:
            flash("Username, password and display name are required.", "error")
            return redirect(url_for("users"))
        if xl.get_user(username):
            flash(f"Username '{username}' already exists.", "error")
            return redirect(url_for("users"))
        if role == "member" and not team_name:
            team_name = display_name
        xl.add_user(username, password, role, display_name,
                    team_name if role == "member" else "")
        flash(f"User '{display_name}' added as {role}.", "success")
        return redirect(url_for("users"))

    @app.route("/users/delete/<username>", methods=["POST"])
    @auth.manager_required
    def delete_user(username: str):
        if username == session.get("username"):
            flash("You cannot delete your own account.", "error")
            return redirect(url_for("users"))
        xl.delete_user(username)
        flash(f"User '{username}' removed.", "success")
        return redirect(url_for("users"))

    @app.route("/users/reset-password/<username>", methods=["POST"])
    @auth.manager_required
    def reset_password(username: str):
        new_pass = request.form.get("new_password", "").strip()
        if not new_pass:
            flash("New password cannot be empty.", "error")
            return redirect(url_for("users"))
        xl.update_user_password(username, new_pass)
        flash(f"Password updated for '{username}'.", "success")
        return redirect(url_for("users"))

    # ── Projects ───────────────────────────────────────────────────────────

    @app.route("/projects")
    @auth.login_required
    def projects():
        return render_template("projects.html", projects=db_backup.get_projects(),
                               members=xl.get_team_members(), is_manager=auth.is_manager())

    @app.route("/projects/add", methods=["POST"])
    @auth.manager_required
    def add_project():
        name = request.form.get("name", "").strip()
        if not name:
            flash("Project name is required.", "error")
            return redirect(url_for("projects"))
        db_backup.add_project(name, request.form.get("description", "").strip(),
                              request.form.get("status", "Active"),
                              request.form.getlist("members"))
        db_backup.log_activity("add_project", session.get("username", ""),
                               session.get("display_name", ""), f"Project: {name}")
        flash(f"Project '{name}' created.", "success")
        return redirect(url_for("projects"))

    @app.route("/projects/edit/<int:pid>", methods=["POST"])
    @auth.manager_required
    def edit_project(pid: int):
        name = request.form.get("name", "").strip()
        if not name:
            flash("Project name is required.", "error")
            return redirect(url_for("projects"))
        db_backup.update_project(pid, name, request.form.get("description", "").strip(),
                                 request.form.get("status", "Active"),
                                 request.form.getlist("members"))
        flash("Project updated.", "success")
        return redirect(url_for("projects"))

    @app.route("/projects/delete/<int:pid>", methods=["POST"])
    @auth.manager_required
    def delete_project(pid: int):
        db_backup.delete_project(pid)
        flash("Project deleted.", "success")
        return redirect(url_for("projects"))

    # ── Discussion ─────────────────────────────────────────────────────────

    @app.route("/discussion", methods=["GET", "POST"])
    @auth.member_required
    def discussion():
        if request.method == "POST":
            note = request.form.get("note", "").strip()
            if note:
                u, d = session.get("username", ""), session.get("display_name", "")
                db_backup.add_note(u, d, note)
                db_backup.log_activity("add_note", u, d, note[:80])
                flash("Note added.", "success")
            return redirect(url_for("discussion"))
        return render_template("discussion.html", notes=db_backup.get_notes())

    # ── Change Password ────────────────────────────────────────────────────

    @app.route("/change-password", methods=["GET", "POST"])
    @auth.login_required
    def change_password():
        if request.method == "POST":
            current  = request.form.get("current_password", "").strip()
            new_pass = request.form.get("new_password", "").strip()
            confirm  = request.form.get("confirm_password", "").strip()
            username = session.get("username", "")
            user = xl.get_user(username)
            if not user or user["password"] != current:
                flash("Current password is incorrect.", "error")
                return redirect(url_for("change_password"))
            if len(new_pass) < 6:
                flash("New password must be at least 6 characters.", "error")
                return redirect(url_for("change_password"))
            if new_pass != confirm:
                flash("New passwords do not match.", "error")
                return redirect(url_for("change_password"))
            xl.update_user_password(username, new_pass)
            db_backup.log_activity("change_password", username,
                                   session.get("display_name", ""), "Password changed")
            flash("Password changed successfully.", "success")
            return redirect(url_for("dashboard"))
        return render_template("change_password.html")

    # ── Backup ─────────────────────────────────────────────────────────────

    @app.route("/backup")
    @auth.manager_required
    def backup_status():
        return render_template("backup.html", tasks=db_backup.get_tasks_backup(),
                               users=db_backup.get_users_backup(),
                               last_sync=db_backup.last_sync_time())

    @app.route("/backup/sync", methods=["POST"])
    @auth.manager_required
    def backup_sync():
        t_count, u_count = db_backup.sync_from_excel()
        flash(f"SQLite synced: {t_count} tasks, {u_count} users.", "success")
        return redirect(url_for("backup_status"))
