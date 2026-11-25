"""Primary store — Postgres (production) or SQLite (local dev)."""
from __future__ import annotations
import os
from contextlib import contextmanager
from datetime import date, datetime

# Re-export project helpers so callers import only db_backup
from db_projects import (get_projects, get_project, add_project,
                         update_project, delete_project)

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_USE_SQLITE = not _DATABASE_URL or _DATABASE_URL.startswith("sqlite:")

# Auto-fallback only when no DATABASE_URL is configured (local dev).
# Never silently fall back when a URL is explicitly set — that hides real errors.

if _USE_SQLITE:
    import db_sqlite_adapter as _sqlite_mod
    _sqlite_path = (
        _DATABASE_URL[len("sqlite:"):] if _DATABASE_URL.startswith("sqlite:")
        else os.path.join(os.path.dirname(__file__), "team_planner.db")
    )
    _sqlite_mod.configure(_sqlite_path)
    get_connection = _sqlite_mod.get_connection
else:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool

    _pg_pool = None

    def _get_pool():
        global _pg_pool
        if _pg_pool is None:
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                1, 5, _DATABASE_URL, connect_timeout=5
            )
        return _pg_pool

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


_db_initialized = False

def init_db():
    global _db_initialized
    if _db_initialized:
        return
    if _USE_SQLITE:
        _init_db_sqlite()
    else:
        _init_db_postgres()
    _db_initialized = True


def _init_db_sqlite():
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, excel_row INTEGER,
            task_date TEXT, team_member TEXT, task TEXT, priority TEXT,
            status TEXT DEFAULT 'Not started', done_looks_like TEXT,
            est_hours REAL, update_7pm TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            is_deleted INTEGER DEFAULT 0,
            assigned_by TEXT DEFAULT ''
        )""")
        # Migrate existing tables that don't have is_deleted yet
        try:
            cur.execute("ALTER TABLE tasks ADD COLUMN is_deleted INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE tasks ADD COLUMN assigned_by TEXT DEFAULT ''")
        except Exception:
            pass
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password TEXT, role TEXT,
            display_name TEXT, team_name TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS discussion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            username TEXT, display_name TEXT, note TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            action TEXT, username TEXT, display_name TEXT, details TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            description TEXT, status TEXT DEFAULT 'Active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS project_members (
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            team_member TEXT, PRIMARY KEY (project_id, team_member)
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uploaded_by TEXT,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            filename TEXT,
            transcript TEXT,
            overall_summary TEXT,
            status TEXT DEFAULT 'processing'
        )""")


def _init_db_postgres():
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY, excel_row INTEGER,
            task_date TEXT, team_member TEXT, task TEXT, priority TEXT,
            status TEXT DEFAULT 'Not started', done_looks_like TEXT,
            est_hours REAL, update_7pm TEXT,
            updated_at TEXT DEFAULT (NOW()::text),
            is_deleted INTEGER DEFAULT 0,
            assigned_by TEXT DEFAULT ''
        )""")
        try:
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_deleted INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS assigned_by TEXT DEFAULT ''")
        except Exception:
            pass
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
        cur.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id SERIAL PRIMARY KEY,
            uploaded_by TEXT,
            uploaded_at TEXT DEFAULT (NOW()::text),
            filename TEXT,
            transcript TEXT,
            overall_summary TEXT,
            status TEXT DEFAULT 'processing',
            audio_data BYTEA,
            audio_mime TEXT
        )""")
        try:
            cur.execute("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS audio_data BYTEA")
            cur.execute("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS audio_mime TEXT")
        except Exception:
            pass


def _row_to_dict(cur, row) -> dict:
    cols = [desc[0] for desc in cur.description]
    return dict(zip(cols, row))


def _fetchall_dicts(cur) -> list[dict]:
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Task CRUD ────────────────────────────────────────────────────────────────

_INCOMPLETE_STATUSES = ("Not started", "Pending", "Blocked", "In progress")
_COMPLETED_STATUSES  = ("Done", "Completed")

def get_tasks_db(filter_date=None, member: str | None = None) -> list[dict]:
    init_db()
    today = date.today()

    def _parse(ds):
        try: return date.fromisoformat(ds) if ds else None
        except (ValueError, TypeError): return ds

    def _row_to_dict(r):
        return {"row": r["excel_row"], "id": r["id"], "date": _parse(r["task_date"]),
                "team_member": r["team_member"], "task": r["task"], "priority": r["priority"],
                "status": r["status"], "done_looks_like": r["done_looks_like"],
                "est_hours": r["est_hours"], "update_7pm": r["update_7pm"],
                "assigned_by": r.get("assigned_by", "")}

    base = "SELECT * FROM tasks WHERE (is_deleted IS NULL OR is_deleted=0)"
    member_clause = " AND team_member=%s" if member else ""

    with get_connection() as con:
        cur = con.cursor()

        if filter_date:
            filter_d = _parse(str(filter_date)) if not isinstance(filter_date, date) else filter_date
            # Fetch tasks assigned on this date
            p = [str(filter_date)]
            if member: p.append(member)
            cur.execute(base + " AND task_date=%s" + member_clause + " ORDER BY task_date, team_member", p)
            rows = _fetchall_dicts(cur)
            result = [_row_to_dict(r) for r in rows]

            # Carry forward: if viewing today (or future), also include incomplete tasks from past dates
            # that are NOT already re-added for this date (avoid duplicates by task text + member)
            if filter_d and filter_d >= today:
                p2 = [str(filter_d)]
                if member: p2.append(member)
                # incomplete tasks from before filter_date
                incomplete_statuses_sql = ",".join(["%s"] * len(_INCOMPLETE_STATUSES))
                p3 = list(_INCOMPLETE_STATUSES) + [str(filter_d)] + ([] if not member else [member])
                cur.execute(
                    base + f" AND status IN ({incomplete_statuses_sql})"
                    " AND task_date<%s" + member_clause +
                    " ORDER BY task_date, team_member",
                    p3
                )
                carry_rows = _fetchall_dicts(cur)
                # Deduplicate: skip if same (member, task) already exists in result for this date
                existing_keys = {(r["team_member"], r["task"].strip().lower()) for r in result}
                for r in carry_rows:
                    key = (r["team_member"], r["task"].strip().lower())
                    if key not in existing_keys:
                        d = _row_to_dict(r)
                        d["carried_forward"] = True  # flag for template use
                        result.append(d)
                        existing_keys.add(key)
            return result
        else:
            # "All dates" view: hide completed tasks from past dates
            if member:
                cur.execute(
                    base + " AND NOT (task_date<%s AND status IN (" +
                    ",".join(["%s"] * len(_COMPLETED_STATUSES)) + "))" +
                    member_clause + " ORDER BY task_date, team_member",
                    [str(today)] + list(_COMPLETED_STATUSES) + [member]
                )
            else:
                cur.execute(
                    base + " AND NOT (task_date<%s AND status IN (" +
                    ",".join(["%s"] * len(_COMPLETED_STATUSES)) + "))" +
                    " ORDER BY task_date, team_member",
                    [str(today)] + list(_COMPLETED_STATUSES)
                )
            rows = _fetchall_dicts(cur)
            return [_row_to_dict(r) for r in rows]


def write_task_db(excel_row: int, task_date, team_member: str, task: str,
                  priority: str, status: str, done_looks_like: str, est_hours, update_7pm: str,
                  assigned_by: str = ""):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        # Only insert if this exact excel_row doesn't exist yet (never overwrite existing rows)
        cur.execute("SELECT id FROM tasks WHERE excel_row=%s AND (is_deleted IS NULL OR is_deleted=0)", (excel_row,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO tasks (excel_row,task_date,team_member,task,priority,"
                "status,done_looks_like,est_hours,update_7pm,assigned_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (excel_row, str(task_date), team_member, task, priority,
                 status, done_looks_like, est_hours, update_7pm, assigned_by))
    _onedrive_push()


def update_task_status_db(excel_row: int, status: str, update_7pm: str):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        # Update only the active (non-deleted) task with this excel_row
        cur.execute("UPDATE tasks SET status=%s, update_7pm=%s WHERE excel_row=%s AND (is_deleted IS NULL OR is_deleted=0)",
                    (status, update_7pm, excel_row))
    _onedrive_push()


def update_task_status_by_id(task_id: int, status: str, update_7pm: str):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("UPDATE tasks SET status=%s, update_7pm=%s WHERE id=%s",
                    (status, update_7pm, task_id))
    _onedrive_push()


def update_task_full_by_id(task_id: int, task_date, team_member: str, task: str,
                            priority: str, status: str, done_looks_like: str, est_hours, update_7pm: str):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE tasks SET task_date=%s,team_member=%s,task=%s,priority=%s,"
            "status=%s,done_looks_like=%s,est_hours=%s,update_7pm=%s WHERE id=%s",
            (str(task_date), team_member, task, priority,
             status, done_looks_like, est_hours, update_7pm, task_id))
    _onedrive_push()


def delete_task_db(excel_row: int):
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("UPDATE tasks SET is_deleted=1 WHERE excel_row=%s", (excel_row,))
    _onedrive_push()


def delete_task_by_id(task_id: int):
    """Soft-delete exactly one task by its unique DB id — never touches other tasks."""
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("UPDATE tasks SET is_deleted=1 WHERE id=%s", (task_id,))
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


def get_history_tasks(days: int = 14) -> list[dict]:
    """Return completed/done tasks from the past N days, newest first."""
    init_db()
    from datetime import timedelta
    today = date.today()
    since = today - timedelta(days=days)
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT * FROM tasks WHERE (is_deleted IS NULL OR is_deleted=0)"
            " AND task_date>=%s AND task_date<%s"
            " ORDER BY task_date DESC, team_member",
            (str(since), str(today))
        )
        rows = _fetchall_dicts(cur)

    def _parse(ds):
        if isinstance(ds, date): return ds
        try: return datetime.strptime(str(ds), "%Y-%m-%d").date()
        except (ValueError, TypeError): return ds

    return [{"id": r["id"], "date": _parse(r["task_date"]),
             "team_member": r["team_member"], "task": r["task"],
             "priority": r["priority"], "status": r["status"],
             "done_looks_like": r["done_looks_like"], "est_hours": r["est_hours"],
             "assigned_by": r.get("assigned_by", "")}
            for r in rows]


def get_all_tasks_history(member: str | None = None) -> list[dict]:
    """Return ALL tasks (not just recent) for the full history page, newest first."""
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        if member:
            cur.execute(
                "SELECT * FROM tasks WHERE (is_deleted IS NULL OR is_deleted=0) AND team_member=%s"
                " ORDER BY task_date DESC, id DESC",
                (member,))
        else:
            cur.execute(
                "SELECT * FROM tasks WHERE (is_deleted IS NULL OR is_deleted=0)"
                " ORDER BY task_date DESC, id DESC")
        rows = _fetchall_dicts(cur)

    def _parse(ds):
        if isinstance(ds, date): return ds
        try: return datetime.strptime(str(ds), "%Y-%m-%d").date()
        except (ValueError, TypeError): return ds

    return [{"id": r["id"], "date": _parse(r["task_date"]),
             "team_member": r["team_member"], "task": r["task"],
             "priority": r["priority"], "status": r["status"],
             "done_looks_like": r["done_looks_like"], "est_hours": r["est_hours"],
             "assigned_by": r.get("assigned_by", ""), "update_7pm": r.get("update_7pm", "")}
            for r in rows]


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
        cur.execute("SELECT * FROM tasks WHERE (is_deleted IS NULL OR is_deleted=0) ORDER BY task_date, team_member")
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


# ── Meeting Upload ────────────────────────────────────────────────────────────

def save_meeting_audio(meeting_id: int, audio_bytes: bytes, mime_type: str):
    with get_connection() as con:
        cur = con.cursor()
        import psycopg2
        cur.execute(
            "UPDATE meetings SET audio_data=%s, audio_mime=%s WHERE id=%s",
            (psycopg2.Binary(audio_bytes), mime_type, meeting_id))


def get_meeting_audio(meeting_id: int) -> dict | None:
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT audio_data, audio_mime, filename FROM meetings WHERE id=%s", (meeting_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {"audio_data": bytes(row[0]) if row[0] else None, "audio_mime": row[1], "filename": row[2]}


def get_all_meetings() -> list:
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT id, uploaded_by, uploaded_at, filename, overall_summary, status FROM meetings ORDER BY id DESC LIMIT 20")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def save_meeting(uploaded_by: str, filename: str) -> int:
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO meetings (uploaded_by, filename) VALUES (%s,%s) RETURNING id",
            (uploaded_by, filename))
        return cur.fetchone()[0]


def update_meeting_result(meeting_id: int, transcript: str, overall_summary: str, status: str = "done"):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE meetings SET transcript=%s, overall_summary=%s, status=%s WHERE id=%s",
            (transcript, overall_summary, status, meeting_id))


def get_latest_meeting() -> dict | None:
    init_db()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM meetings ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None


def add_auto_task(task_date, member: str, task: str, priority: str, meeting_id: int):
    """Insert an AI-assigned task into the tasks table with a generated excel_row."""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT COALESCE(MAX(excel_row), 80000) + 1 FROM tasks")
        next_row = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO tasks (excel_row, task_date, team_member, task, priority, status, update_7pm)"
            " VALUES (%s,%s,%s,%s,%s,'Not started',%s)",
            (next_row, str(task_date), member, task, priority,
             f"[Auto-assigned from meeting #{meeting_id}]"))
        return next_row
