"""SQLite CRUD for projects and project_members tables."""
from __future__ import annotations
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "team_planner.db"


def _conn():
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def get_projects() -> list[dict]:
    with _conn() as con:
        projects = [dict(r) for r in con.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall()]
        for p in projects:
            rows = con.execute(
                "SELECT team_member FROM project_members WHERE project_id=?", (p["id"],)
            ).fetchall()
            p["members"] = [r["team_member"] for r in rows]
    return projects


def get_project(pid: int) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        p = dict(row)
        rows = con.execute(
            "SELECT team_member FROM project_members WHERE project_id=?", (pid,)
        ).fetchall()
        p["members"] = [r["team_member"] for r in rows]
    return p


def add_project(name: str, description: str, status: str, members: list[str]) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO projects (name, description, status) VALUES (?,?,?)",
            (name, description, status),
        )
        pid = cur.lastrowid
        for m in members:
            con.execute("INSERT OR IGNORE INTO project_members VALUES (?,?)", (pid, m))
        con.commit()
    return pid


def update_project(pid: int, name: str, description: str, status: str, members: list[str]):
    with _conn() as con:
        con.execute("UPDATE projects SET name=?, description=?, status=? WHERE id=?",
                    (name, description, status, pid))
        con.execute("DELETE FROM project_members WHERE project_id=?", (pid,))
        for m in members:
            con.execute("INSERT OR IGNORE INTO project_members VALUES (?,?)", (pid, m))
        con.commit()


def delete_project(pid: int):
    with _conn() as con:
        con.execute("DELETE FROM project_members WHERE project_id=?", (pid,))
        con.execute("DELETE FROM projects WHERE id=?", (pid,))
        con.commit()
