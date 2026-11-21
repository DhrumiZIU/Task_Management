"""SQLite primary store — tasks/users synced from Excel; discussion + activity web-only."""
from __future__ import annotations
import os, sqlite3, threading
from datetime import date, datetime
from pathlib import Path

DB_PATH    = Path(__file__).resolve().parent / "team_planner.db"
EXCEL_PATH = Path(__file__).resolve().parent / "Team Daily Planner.xlsx"
_LOCK      = threading.Lock()   # serialises Excel sync
_DB_WRITE  = threading.Lock()   # serialises concurrent SQLite writes (WAL handles reads)

# Re-export project helpers so callers import only db_backup
from db_projects import (get_projects, get_project, add_project,
                         update_project, delete_project)


def _conn():
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")   # concurrent reads + one writer, no blocking
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, excel_row INTEGER,
            task_date TEXT, team_member TEXT, task TEXT, priority TEXT,
            status TEXT DEFAULT 'Not started', done_looks_like TEXT,
            est_hours REAL, update_7pm TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password TEXT, role TEXT,
            display_name TEXT, team_name TEXT
        );
        CREATE TABLE IF NOT EXISTS discussion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now','localtime')),
            username TEXT, display_name TEXT, note TEXT
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now','localtime')),
            action TEXT, username TEXT, display_name TEXT, details TEXT
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            description TEXT, status TEXT DEFAULT 'Active',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS project_members (
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            team_member TEXT, PRIMARY KEY (project_id, team_member)
        );
        CREATE TABLE IF NOT EXISTS sync_meta (
            id INTEGER PRIMARY KEY, last_sync TEXT, excel_mtime REAL DEFAULT 0
        );
        """)
        con.execute("INSERT OR IGNORE INTO sync_meta (id,last_sync,excel_mtime) VALUES (1,NULL,0)")
        con.commit()


# ── Excel ↔ SQLite sync ──────────────────────────────────────────────────────

def _excel_mtime() -> float:
    try: return os.path.getmtime(EXCEL_PATH)
    except OSError: return 0.0


def _stored_mtime() -> float:
    with _conn() as con:
        row = con.execute("SELECT excel_mtime FROM sync_meta WHERE id=1").fetchone()
    return row["excel_mtime"] if row else 0.0


def sync_from_excel() -> tuple[int, int]:
    import excel_utils as xl
    init_db()
    tasks, users, mtime = xl.get_tasks(), xl.get_all_users(), _excel_mtime()
    with _conn() as con:
        con.execute("DELETE FROM tasks"); con.execute("DELETE FROM users")
        for t in tasks:
            d = t["date"]
            ds = d.isoformat() if isinstance(d, (date, datetime)) else str(d or "")
            con.execute("INSERT INTO tasks (excel_row,task_date,team_member,task,priority,"
                        "status,done_looks_like,est_hours,update_7pm) VALUES(?,?,?,?,?,?,?,?,?)",
                        (t["row"],ds,t["team_member"],t["task"],t["priority"],
                         t["status"],t["done_looks_like"],t["est_hours"],t["update_7pm"]))
        for u in users:
            con.execute("INSERT OR REPLACE INTO users (username,password,role,display_name,team_name)"
                        " VALUES(?,?,?,?,?)",
                        (u["username"],u["password"],u["role"],u["display_name"],u["team_name"]))
        con.execute("UPDATE sync_meta SET last_sync=datetime('now','localtime'),excel_mtime=? WHERE id=1", (mtime,))
    return len(tasks), len(users)


def sync_if_excel_changed():
    if _excel_mtime() > _stored_mtime():
        threading.Thread(target=_safe_sync, daemon=True).start()


def _safe_sync():
    with _LOCK:
        try: sync_from_excel()
        except Exception: pass


# ── Task CRUD (SQLite as source of truth for web app) ────────────────────────

def get_tasks_db(filter_date=None, member: str | None = None) -> list[dict]:
    init_db()
    q, p = "SELECT * FROM tasks WHERE 1=1", []
    if filter_date:
        q += " AND task_date=?"; p.append(str(filter_date))
    if member:
        q += " AND team_member=?"; p.append(member)
    q += " ORDER BY task_date, team_member"
    with _conn() as con:
        rows = con.execute(q, p).fetchall()
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
    with _DB_WRITE, _conn() as con:
        con.execute("DELETE FROM tasks WHERE excel_row=?", (excel_row,))
        con.execute("INSERT INTO tasks (excel_row,task_date,team_member,task,priority,"
                    "status,done_looks_like,est_hours,update_7pm) VALUES(?,?,?,?,?,?,?,?,?)",
                    (excel_row, str(task_date), team_member, task, priority,
                     status, done_looks_like, est_hours, update_7pm))
        con.commit()

def update_task_status_db(excel_row: int, status: str, update_7pm: str):
    init_db()
    with _DB_WRITE, _conn() as con:
        con.execute("UPDATE tasks SET status=?,update_7pm=? WHERE excel_row=?",
                    (status, update_7pm, excel_row)); con.commit()

def delete_task_db(excel_row: int):
    init_db()
    with _DB_WRITE, _conn() as con:
        con.execute("DELETE FROM tasks WHERE excel_row=?", (excel_row,)); con.commit()


# ── Discussion ───────────────────────────────────────────────────────────────

def get_notes() -> list[dict]:
    init_db()
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM discussion ORDER BY id DESC").fetchall()]


def add_note(username: str, display_name: str, note: str):
    init_db()
    with _DB_WRITE, _conn() as con:
        con.execute("INSERT INTO discussion (username,display_name,note) VALUES(?,?,?)",
                    (username, display_name, note)); con.commit()

def log_activity(action: str, username: str, display_name: str, details: str = ""):
    init_db()
    with _DB_WRITE, _conn() as con:
        con.execute("INSERT INTO activity_log (action,username,display_name,details) VALUES(?,?,?,?)",
                    (action, username, display_name, details)); con.commit()

def get_activity_log(limit: int = 100) -> list[dict]:
    init_db()
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

def get_tasks_backup() -> list[dict]:
    init_db()
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM tasks ORDER BY task_date,team_member").fetchall()]

def get_users_backup() -> list[dict]:
    init_db()
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM users ORDER BY role,display_name").fetchall()]

def last_sync_time() -> str:
    init_db()
    with _conn() as con:
        row = con.execute("SELECT last_sync FROM sync_meta WHERE id=1").fetchone()
    return row["last_sync"] if row and row["last_sync"] else "Never"
