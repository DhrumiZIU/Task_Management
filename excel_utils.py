"""All openpyxl read/write helpers. Excel is the single source of truth.
When Excel is absent (e.g. on Railway), all operations fall back to SQLite."""
from __future__ import annotations
import threading
from datetime import date, datetime
from pathlib import Path
from openpyxl import load_workbook

WORKBOOK_PATH = Path(__file__).resolve().parent / "Team Daily Planner.xlsx"
EXCEL_AVAILABLE = False  # always False on Vercel; Excel path is not writable
LOCK = threading.RLock()
TASK_COLS = {"date":1,"team_member":2,"task":3,"priority":4,
             "status":5,"done_looks_like":6,"est_hours":7,"update_7pm":8}
USER_COLS = {"username":1,"password":2,"role":3,"display_name":4,"team_name":5}
DATA_START, MAX_ROWS = 2, 3000


def _load(): return load_workbook(WORKBOOK_PATH)


def _sync():
    """Background full-sync Excel → SQLite (called only on startup or manual request)."""
    try:
        import db_backup
        threading.Thread(target=db_backup.sync_from_excel, daemon=True).start()
    except Exception: pass


def _ensure_users_sheet(wb):
    if "Users" in wb.sheetnames: return
    ws = wb.create_sheet("Users")
    for col, h in enumerate(["username","password","role","display_name","team_name"],1):
        ws.cell(1, col, h)
    defaults = [
        ("manager1","manager1pass","manager","Manager 1",""),
        ("manager2","manager2pass","manager","Manager 2",""),
        ("member1","member1pass","member","Member 1","Member 1"),
        ("member2","member2pass","member","Member 2","Member 2"),
        ("member3","member3pass","member","Member 3","Member 3"),
        ("member4","member4pass","member","Member 4","Member 4"),
        ("member5","member5pass","member","Member 5","Member 5"),
    ]
    for i, row in enumerate(defaults, 2):
        for j, val in enumerate(row, 1): ws.cell(i, j, val)
    wb.save(WORKBOOK_PATH)


def get_all_users() -> list[dict]:
    if not EXCEL_AVAILABLE:
        import db_backup
        return db_backup.get_users_backup()
    with LOCK:
        wb = _load(); _ensure_users_sheet(wb); ws = wb["Users"]
        return [{"username":r[0],"password":r[1],"role":r[2],
                 "display_name":r[3],"team_name":r[4] or ""}
                for r in ws.iter_rows(min_row=DATA_START, values_only=True) if r[0]]


def get_user(username: str) -> dict | None:
    if not EXCEL_AVAILABLE:
        import db_backup
        return db_backup.get_user_db(username)
    return next((u for u in get_all_users() if u["username"] == username), None)


def _next_empty_user_row(ws) -> int:
    for row in ws.iter_rows(min_row=DATA_START, max_row=200):
        if not row[0].value: return row[0].row
    return 202


def add_user(username: str, password: str, role: str, display_name: str, team_name: str = ""):
    if not EXCEL_AVAILABLE:
        import db_backup
        with db_backup.get_connection() as con:
            cur = con.cursor()
            cur.execute(
                "INSERT INTO users (username,password,role,display_name,team_name)"
                " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (username) DO UPDATE SET"
                " password=EXCLUDED.password, role=EXCLUDED.role,"
                " display_name=EXCLUDED.display_name, team_name=EXCLUDED.team_name",
                (username, password, role, display_name, team_name))
        return
    with LOCK:
        wb = _load(); ws = wb["Users"]
        row = _next_empty_user_row(ws)
        for col, val in zip(USER_COLS.values(), [username,password,role,display_name,team_name]):
            ws.cell(row, col).value = val
        if role == "member" and team_name: _add_team_member(wb, team_name, role)
        wb.save(WORKBOOK_PATH); _sync()


def delete_user(username: str):
    if not EXCEL_AVAILABLE:
        import db_backup
        with db_backup.get_connection() as con:
            con.cursor().execute("DELETE FROM users WHERE username=%s", (username,))
        return
    with LOCK:
        wb = _load(); ws = wb["Users"]
        for row in ws.iter_rows(min_row=DATA_START, max_row=200):
            if row[0].value == username:
                team_name, role = row[4].value, row[2].value
                for cell in row: cell.value = None
                if role == "member" and team_name: _remove_team_member(wb, team_name)
                break
        wb.save(WORKBOOK_PATH); _sync()


def update_user_password(username: str, new_password: str):
    if not EXCEL_AVAILABLE:
        import db_backup
        with db_backup.get_connection() as con:
            con.cursor().execute("UPDATE users SET password=%s WHERE username=%s", (new_password, username))
        return
    with LOCK:
        wb = _load(); ws = wb["Users"]
        for row in ws.iter_rows(min_row=DATA_START, max_row=200):
            if row[0].value == username: row[1].value = new_password; break
        wb.save(WORKBOOK_PATH); _sync()


def update_user_profile(old_username: str, new_username: str, new_display_name: str, new_password: str | None = None):
    """Update username, display_name, and optionally password."""
    import db_backup
    db_backup.update_profile_db(old_username, new_username, new_display_name, new_password)
    if EXCEL_AVAILABLE:
        with LOCK:
            wb = _load()
            ws = wb["Users"]
            for row in ws.iter_rows(min_row=DATA_START, max_row=200):
                if row[0].value == old_username:
                    row[0].value = new_username
                    row[3].value = new_display_name
                    if new_password: row[1].value = new_password
                    break
            wb.save(WORKBOOK_PATH)


def _add_team_member(wb, name: str, role: str = "member"):
    ws = wb["Team"]
    for row in ws.iter_rows(min_row=DATA_START, max_row=51):
        if not row[0].value: row[0].value = name; row[1].value = role; return


def _remove_team_member(wb, name: str):
    ws = wb["Team"]
    for row in ws.iter_rows(min_row=DATA_START, max_row=51):
        if row[0].value == name: row[0].value = None; row[1].value = None; return


def get_team_members() -> list[str]:
    if not EXCEL_AVAILABLE:
        import db_backup
        with db_backup.get_connection() as con:
            cur = con.cursor()
            cur.execute(
                "SELECT DISTINCT team_name FROM users WHERE role='member' AND team_name!='' ORDER BY team_name"
            )
            return [r[0] for r in cur.fetchall()]
    with LOCK:
        wb = _load(); ws = wb["Team"]
        return [r[0] for r in ws.iter_rows(min_row=DATA_START, max_row=51, values_only=True) if r[0]]


def get_tasks(filter_date: date | None = None, member: str | None = None) -> list[dict]:
    if not EXCEL_AVAILABLE:
        import db_backup
        return db_backup.get_tasks_db(filter_date=filter_date, member=member)
    with LOCK:
        wb = _load(); ws = wb["Tasks"]; tasks = []
        for i, row in enumerate(
            ws.iter_rows(min_row=DATA_START, max_row=MAX_ROWS, values_only=True), start=DATA_START
        ):
            if not row[0] or not row[2]: continue   # skip rows missing date or task text
            row_date = row[0].date() if isinstance(row[0], datetime) else row[0]
            if filter_date and row_date != filter_date: continue
            if member and row[1] != member: continue
            tasks.append({"row":i,"date":row_date,"team_member":row[1],"task":row[2],
                          "priority":row[3],"status":row[4],"done_looks_like":row[5],
                          "est_hours":row[6],"update_7pm":row[7]})
        return tasks


def add_task(task_date: date, team_member: str, task: str, priority: str,
             done_looks_like: str = "", est_hours: float | None = None) -> int:
    if not EXCEL_AVAILABLE:
        import db_backup
        with db_backup.get_connection() as con:
            cur = con.cursor()
            cur.execute("SELECT MAX(excel_row) FROM tasks")
            row = cur.fetchone()
            row_num = (row[0] or 1) + 1
        db_backup.write_task_db(row_num, task_date, team_member, task, priority,
                                "Not started", done_looks_like, est_hours, "")
        return row_num
    with LOCK:
        wb = _load(); ws = wb["Tasks"]; row_num = DATA_START
        for row in ws.iter_rows(min_row=DATA_START, max_row=MAX_ROWS):
            if row[0].value is None: row_num = row[0].row; break
        ws.cell(row_num, TASK_COLS["date"]).value          = task_date
        ws.cell(row_num, TASK_COLS["team_member"]).value   = team_member
        ws.cell(row_num, TASK_COLS["task"]).value          = task
        ws.cell(row_num, TASK_COLS["priority"]).value      = priority
        ws.cell(row_num, TASK_COLS["status"]).value        = "Not started"
        ws.cell(row_num, TASK_COLS["done_looks_like"]).value = done_looks_like
        if est_hours is not None: ws.cell(row_num, TASK_COLS["est_hours"]).value = est_hours
        wb.save(WORKBOOK_PATH); _sync()
        return row_num


def update_task_full(row_num: int, task: str, priority: str, status: str,
                     done_looks_like: str, est_hours: float | None, update_7pm: str):
    if not EXCEL_AVAILABLE:
        import db_backup
        with db_backup.get_connection() as con:
            con.cursor().execute(
                "UPDATE tasks SET task=%s, priority=%s, status=%s, done_looks_like=%s,"
                " est_hours=%s, update_7pm=%s WHERE excel_row=%s",
                (task, priority, status, done_looks_like, est_hours, update_7pm, row_num))
        return
    with LOCK:
        wb = _load(); ws = wb["Tasks"]
        for col, val in zip(
            [TASK_COLS[k] for k in ("task","priority","status","done_looks_like","est_hours","update_7pm")],
            [task, priority, status, done_looks_like, est_hours, update_7pm]
        ):
            ws.cell(row_num, col).value = val
        wb.save(WORKBOOK_PATH); _sync()


def update_task_status(row_num: int, status: str, update_7pm: str):
    if not EXCEL_AVAILABLE:
        import db_backup
        db_backup.update_task_status_db(row_num, status, update_7pm)
        return
    with LOCK:
        wb = _load(); ws = wb["Tasks"]
        ws.cell(row_num, TASK_COLS["status"]).value    = status
        ws.cell(row_num, TASK_COLS["update_7pm"]).value = update_7pm
        wb.save(WORKBOOK_PATH); _sync()


def delete_task(row_num: int):
    if not EXCEL_AVAILABLE:
        import db_backup
        db_backup.delete_task_db(row_num)
        return
    with LOCK:
        wb = _load(); ws = wb["Tasks"]
        for col in range(1, 9): ws.cell(row_num, col).value = None
        wb.save(WORKBOOK_PATH); _sync()
