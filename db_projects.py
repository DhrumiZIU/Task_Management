"""Postgres CRUD for projects and project_members tables."""
from __future__ import annotations


def _conn():
    from db_backup import get_connection
    return get_connection()


def get_projects() -> list[dict]:
    from db_backup import get_connection
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
        cols = [d[0] for d in cur.description]
        projects = [dict(zip(cols, row)) for row in cur.fetchall()]
        for p in projects:
            cur.execute(
                "SELECT team_member FROM project_members WHERE project_id=%s", (p["id"],)
            )
            p["members"] = [r[0] for r in cur.fetchall()]
    return projects


def get_project(pid: int) -> dict | None:
    from db_backup import get_connection
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM projects WHERE id=%s", (pid,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        p = dict(zip(cols, row))
        cur.execute(
            "SELECT team_member FROM project_members WHERE project_id=%s", (pid,)
        )
        p["members"] = [r[0] for r in cur.fetchall()]
    return p


def add_project(name: str, description: str, status: str, members: list[str]) -> int:
    from db_backup import get_connection
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO projects (name, description, status) VALUES (%s,%s,%s) RETURNING id",
            (name, description, status),
        )
        pid = cur.fetchone()[0]
        for m in members:
            cur.execute(
                "INSERT INTO project_members VALUES (%s,%s) ON CONFLICT DO NOTHING", (pid, m)
            )
    return pid


def update_project(pid: int, name: str, description: str, status: str, members: list[str]):
    from db_backup import get_connection
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE projects SET name=%s, description=%s, status=%s WHERE id=%s",
            (name, description, status, pid),
        )
        cur.execute("DELETE FROM project_members WHERE project_id=%s", (pid,))
        for m in members:
            cur.execute(
                "INSERT INTO project_members VALUES (%s,%s) ON CONFLICT DO NOTHING", (pid, m)
            )


def delete_project(pid: int):
    from db_backup import get_connection
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM project_members WHERE project_id=%s", (pid,))
        cur.execute("DELETE FROM projects WHERE id=%s", (pid,))
