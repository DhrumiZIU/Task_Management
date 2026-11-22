"""SQLite primary store — tasks/users synced from Excel; discussion + activity web-only."""
from __future__ import annotations
import os, sqlite3, threading
from datetime import date, datetime
from pathlib import Path


def _onedrive_push():
    """Fire-and-forget OneDrive sync after any data write (no-op if not configured)."""
    try:
        import onedrive_sync
        onedrive_sync.push_to_onedrive()
    except Exception:
        pass

# DATA_DIR: use Railway volume mount if set, otherwise the project directory
_DATA_DIR  = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent)))
_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH    = _DATA_DIR / "team_planner.db"
EXCEL_PATH = _DATA_DIR / "Team Daily Planner.xlsx"
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
    _onedrive_push()

def update_task_status_db(excel_row: int, status: str, update_7pm: str):
    init_db()
    with _DB_WRITE, _conn() as con:
        con.execute("UPDATE tasks SET status=?,update_7pm=? WHERE excel_row=?",
                    (status, update_7pm, excel_row)); con.commit()
    _onedrive_push()

def delete_task_db(excel_row: int):
    init_db()
    with _DB_WRITE, _conn() as con:
        con.execute("DELETE FROM tasks WHERE excel_row=?", (excel_row,)); con.commit()
    _onedrive_push()


# ── Discussion ───────────────────────────────────────────────────────────────

def get_notes(username: str) -> list[dict]:
    """Return only the notes belonging to this user."""
    init_db()
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM discussion WHERE username=? ORDER BY id DESC", (username,)).fetchall()]


def add_note(username: str, display_name: str, note: str):
    init_db()
    with _DB_WRITE, _conn() as con:
        con.execute("INSERT INTO discussion (username,display_name,note) VALUES(?,?,?)",
                    (username, display_name, note)); con.commit()


def delete_note(note_id: int, username: str) -> bool:
    """Delete a note only if it belongs to the given user. Returns True if deleted."""
    init_db()
    with _DB_WRITE, _conn() as con:
        cur = con.execute("DELETE FROM discussion WHERE id=? AND username=?", (note_id, username))
        con.commit()
    return cur.rowcount > 0

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

def get_user_db(username: str) -> dict | None:
    """Read one user from SQLite — used by auth (works without Excel on Render)."""
    init_db()
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(row) if row else None


def update_profile_db(old_username: str, new_username: str, new_display_name: str, new_password: str | None = None):
    """Rename username, display_name, and optionally password in SQLite."""
    init_db()
    with _DB_WRITE, _conn() as con:
        if new_password:
            con.execute(
                "UPDATE users SET username=?,display_name=?,team_name=CASE WHEN role='member' THEN ? ELSE team_name END,password=? WHERE username=?",
                (new_username, new_display_name, new_display_name, new_password, old_username))
        else:
            con.execute(
                "UPDATE users SET username=?,display_name=?,team_name=CASE WHEN role='member' THEN ? ELSE team_name END WHERE username=?",
                (new_username, new_display_name, new_display_name, old_username))
        # Keep task records in sync with the new display_name (member tasks use team_name)
        con.execute("UPDATE tasks SET team_member=? WHERE team_member=?",
                    (new_display_name, new_display_name))
        con.commit()


def seed_users():
    """Insert/update default users on every startup so credential changes deploy automatically."""
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
    old_usernames = ["manager1","manager2","member1","member2","member3","member4","member5"]
    with _conn() as con:
        # Remove old default accounts if still present
        con.executemany("DELETE FROM users WHERE username=?", [(u,) for u in old_usernames])
        con.executemany(
            "INSERT OR IGNORE INTO users (username,password,role,display_name,team_name)"
            " VALUES (?,?,?,?,?)", defaults)
        con.commit()


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
