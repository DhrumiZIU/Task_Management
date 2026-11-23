"""Postgres primary store — tasks/users/discussion/activity via psycopg2."""
from __future__ import annotations
import os
from contextlib import contextmanager
from datetime import date, datetime

import psycopg2
import psycopg2.extras
import psycopg2.pool

# Re-export project helpers so callers import only db_backup
from db_projects import (get_projects, get_project, add_project,
                         update_project, delete_project)

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 5, _DATABASE_URL)
    return _pool


@contextmanager
def get_connection():
    pool = _get_pool()
    con = pool.getconn()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        pool.putconn(con)


def _onedrive_push():
    try:
        import onedrive_sync
        onedrive_sync.push_to_onedrive()
    except Exception:
        pass


def init_db():
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY, excel_row INTEGER,
            task_date TEXT, team_member TEXT, task TEXT, priority TEXT,
            status TEXT DEFAULT 'Not started', done_looks_like TEXT,
            est_hours REAL, update_7pm TEXT,
            updated_at TEXT DEFAULT (NOW()::text)
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password TEXT, role TEXT,
            display_name TEXT, team_name TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS discussion (
            id SERIAL PRIMARY KEY,
            timestamp TEXT DEFAULT (NOW()::text),
            username TEXT, display_name TEXT, note TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id SERIAL PRIMARY KEY,
            timestamp TEXT DEFAULT (NOW()::text),
            action TEXT, username TEXT, display_name TEXT, details TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL,
            description TEXT, status TEXT DEFAULT 'Active',
            created_at TEXT DEFAULT (NOW()::text)
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS project_members (
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            team_member TEXT, PRIMARY KEY (project_id, team_member)
        )""")


def _row_to_dict(cur, row) -> dict:
    cols = [desc[0] for desc in cur.description]
    return dict(zip(cols, row))


def _fetchall_dicts(cur) -> list[dict]:
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Task CRUD ────────────────────────────────────────────────────────────────

def get_tasks_db(filter_date=None, member: str | None = None) -> list[dict]:
    init_db()
    q, p = "SELECT * FROM tasks WHERE 1=1", []
    if filter_date:
        q += " AND task_date=%s"; p.append(str(filter_date))
    if member:
        q += " AND team_member=%s"; p.append(member)
    q += " ORDER BY task_date, team_member"
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(q, p)
        rows = _fetchall_dicts(cur)
    def _parse(ds):
        try: return date.fromisoformat(ds) if ds else None
        except (ValueError, TypeError): return ds
    return [{"row": r["excel_row"], "date": _parse(r["task_date"]), "team_member": r["team_member"],
             "task": r["task"], "priority": r["priority"], "status": r["status"],
             "done_looks_like": r["done_looks_like"], "est_hours": r["est_hours"],
             "update_7pm": r["update_7pm"]} for r in rows]


def write_task_db(excel_row: int, task_date, team_member: str, task: str,
                  priority: str, status: str, done_looks_like: str, est_hours, update_7pm: str):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM tasks WHERE excel_row=%s", (excel_row,))
        cur.execute(
            "INSERT INTO tasks (excel_row,task_date,team_member,task,priority,"
            "status,done_looks_like,est_hours,update_7pm) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (excel_row, str(task_date), team_member, task, priority,
             status, done_looks_like, est_hours, update_7pm))
    _onedrive_push()


def update_task_status_db(excel_row: int, status: str, update_7pm: str):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("UPDATE tasks SET status=%s, update_7pm=%s WHERE excel_row=%s",
                    (status, update_7pm, excel_row))
    _onedrive_push()


def delete_task_db(excel_row: int):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM tasks WHERE excel_row=%s", (excel_row,))
    _onedrive_push()


# ── Discussion / Notes ───────────────────────────────────────────────────────

def get_notes(username: str) -> list[dict]:
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM discussion WHERE username=%s ORDER BY id DESC", (username,))
        return _fetchall_dicts(cur)


def add_note(username: str, display_name: str, note: str):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("INSERT INTO discussion (username,display_name,note) VALUES(%s,%s,%s)",
                    (username, display_name, note))


def delete_note(note_id: int, username: str) -> bool:
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM discussion WHERE id=%s AND username=%s", (note_id, username))
        return cur.rowcount > 0


def log_activity(action: str, username: str, display_name: str, details: str = ""):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("INSERT INTO activity_log (action,username,display_name,details) VALUES(%s,%s,%s,%s)",
                    (action, username, display_name, details))


def get_activity_log(limit: int = 100) -> list[dict]:
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT %s", (limit,))
        return _fetchall_dicts(cur)


# ── Users ────────────────────────────────────────────────────────────────────

def get_user_db(username: str) -> dict | None:
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None


def update_profile_db(old_username: str, new_username: str, new_display_name: str, new_password: str | None = None):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        if new_password:
            cur.execute(
                "UPDATE users SET username=%s, display_name=%s, "
                "team_name=CASE WHEN role='member' THEN %s ELSE team_name END, "
                "password=%s WHERE username=%s",
                (new_username, new_display_name, new_display_name, new_password, old_username))
        else:
            cur.execute(
                "UPDATE users SET username=%s, display_name=%s, "
                "team_name=CASE WHEN role='member' THEN %s ELSE team_name END "
                "WHERE username=%s",
                (new_username, new_display_name, new_display_name, old_username))
        cur.execute("UPDATE tasks SET team_member=%s WHERE team_member=%s",
                    (new_display_name, new_display_name))


def get_users_backup() -> list[dict]:
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM users ORDER BY role, display_name")
        return _fetchall_dicts(cur)


def get_tasks_backup() -> list[dict]:
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM tasks ORDER BY task_date, team_member")
        return _fetchall_dicts(cur)


def last_sync_time() -> str:
    return "N/A (Postgres)"


def seed_users():
    """Insert default users on startup; skip if already present."""
    init_db()
    defaults = [
        ("AdityaNaik",     "Aditya@123",  "manager", "Aditya Naik",     ""),
        ("SachinSahane",   "Sachin@123",  "manager", "Sachin Sahane",   ""),
        ("KathaPawale",    "Katha@123",   "member",  "Katha Pawale",    "Katha Pawale"),
        ("DrashtiMakwana", "Drashti@123", "member",  "Drashti Makwana", "Drashti Makwana"),
        ("DhrumiKhatri",   "Dhrumi@123",  "member",  "Dhrumi Khatri",   "Dhrumi Khatri"),
        ("HarshMarodia",   "Harsh@123",   "member",  "Harsh Marodia",   "Harsh Marodia"),
        ("MohitJain",      "Mohit@123",   "member",  "Mohit Jain",      "Mohit Jain"),
    ]
    old_usernames = ["manager1", "manager2", "member1", "member2", "member3", "member4", "member5"]
    with get_connection() as con:
        cur = con.cursor()
        for u in old_usernames:
            cur.execute("DELETE FROM users WHERE username=%s", (u,))
        for row in defaults:
            cur.execute(
                "INSERT INTO users (username,password,role,display_name,team_name)"
                " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (username) DO NOTHING", row)
