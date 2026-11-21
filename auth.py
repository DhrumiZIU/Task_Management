"""Session-based auth + permission helpers. All user data comes from Excel."""
from __future__ import annotations

from functools import wraps

from flask import redirect, session, url_for

import excel_utils as xl


def login_user(username: str, password: str) -> bool:
    user = xl.get_user(username)
    if user and user["password"] == password:
        session["username"] = username
        session["role"] = user["role"]
        session["display_name"] = user["display_name"]
        session["team_name"] = user.get("team_name", "")
        return True
    return False


def logout_user():
    session.clear()


def is_manager() -> bool:
    return session.get("role") == "manager"


def can_edit_task(task_member: str) -> bool:
    """Manager edits all; member edits only their own."""
    if is_manager():
        return True
    return session.get("team_name") == task_member


def can_delete_task() -> bool:
    return is_manager()


def can_manage_users() -> bool:
    return is_manager()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def manager_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        if not is_manager():
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


def member_required(f):
    """Only team members (role=member) can access this route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        if is_manager():
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated
