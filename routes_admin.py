"""Users, projects, discussion and backup routes — registered onto the main app."""
from __future__ import annotations
import re
from flask import flash, redirect, render_template, request, session, url_for
import auth
import excel_utils as xl
import db_backup
from config import ROLE_OPTIONS


def _validate_password(pw: str) -> str | None:
    """Return an error message, or None if the password is valid."""
    if len(pw) < 6:
        return "Password must be at least 6 characters."
    if not re.search(r"[A-Z]", pw):
        return "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", pw):
        return "Password must contain at least one lowercase letter."
    if not re.search(r"\d", pw):
        return "Password must contain at least one number."
    if not re.search(r"[^A-Za-z0-9]", pw):
        return "Password must contain at least one special character (e.g. @, #, !)."
    return None


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
        err = _validate_password(password)
        if err:
            flash(err, "error")
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
    @auth.login_required
    def discussion():
        if session.get("role") != "manager":
            flash("Notes are only available to managers.", "error")
            return redirect(url_for("dashboard"))
        u, d = session.get("username", ""), session.get("display_name", "")
        if request.method == "POST":
            note = request.form.get("note", "").strip()
            if note:
                db_backup.add_note(u, d, note)
                db_backup.log_activity("add_note", u, d, note[:80])
                flash("Note added.", "success")
            return redirect(url_for("discussion"))
        return render_template("discussion.html", notes=db_backup.get_notes(u))

    @app.route("/discussion/delete/<int:note_id>", methods=["POST"])
    @auth.login_required
    def delete_note(note_id: int):
        deleted = db_backup.delete_note(note_id, session.get("username", ""))
        if not deleted:
            flash("Note not found or not yours.", "error")
        return redirect(url_for("discussion"))

    # ── Change Password / Profile ──────────────────────────────────────────

    @app.route("/change-password", methods=["GET", "POST"])
    @auth.login_required
    def change_password():
        if request.method == "POST":
            current      = request.form.get("current_password", "").strip()
            new_pass     = request.form.get("new_password", "").strip()
            confirm      = request.form.get("confirm_password", "").strip()
            new_username = request.form.get("new_username", "").strip()
            new_dispname = request.form.get("new_display_name", "").strip()
            username     = session.get("username", "")

            user = xl.get_user(username)
            if not user or user["password"] != current:
                flash("Current password is incorrect.", "error")
                return redirect(url_for("change_password"))

            # Validate new username
            if not new_username:
                flash("Username cannot be empty.", "error")
                return redirect(url_for("change_password"))
            if not new_dispname:
                flash("Display name cannot be empty.", "error")
                return redirect(url_for("change_password"))
            # Check username collision (allow keeping same username)
            if new_username != username and xl.get_user(new_username):
                flash(f"Username '{new_username}' is already taken.", "error")
                return redirect(url_for("change_password"))

            # Password change is optional — only validate if user typed something
            validated_pass = None
            if new_pass or confirm:
                err = _validate_password(new_pass)
                if err:
                    flash(err, "error")
                    return redirect(url_for("change_password"))
                if new_pass != confirm:
                    flash("New passwords do not match.", "error")
                    return redirect(url_for("change_password"))
                validated_pass = new_pass

            xl.update_user_profile(username, new_username, new_dispname, validated_pass)
            session["username"]     = new_username
            session["display_name"] = new_dispname
            db_backup.log_activity("update_profile", new_username, new_dispname,
                                   "Profile updated" + (" + password" if validated_pass else ""))
            flash("Profile updated successfully.", "success")
            return redirect(url_for("dashboard"))
        return render_template("change_password.html",
                               current_username=session.get("username", ""),
                               current_display=session.get("display_name", ""))

    @app.route("/settings")
    @auth.login_required
    def settings():
        return render_template("settings.html",
                               current_username=session.get("username", ""),
                               current_display=session.get("display_name", ""),
                               role=session.get("role", ""))

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

    @app.route("/onedrive/push", methods=["POST"])
    @auth.manager_required
    def onedrive_push():
        """Manually push latest data to the manager's OneDrive."""
        import onedrive_sync
        if not onedrive_sync.is_configured():
            flash(
                "OneDrive sync is not configured. "
                "Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET "
                "and ONEDRIVE_USER_EMAIL in Railway environment variables.",
                "error",
            )
            return redirect(url_for("backup_status"))
        ok, msg = onedrive_sync.push_to_onedrive_sync()
        flash(msg, "success" if ok else "error")
        return redirect(url_for("backup_status"))
