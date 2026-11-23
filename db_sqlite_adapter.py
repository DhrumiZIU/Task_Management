"""SQLite adapter that mimics the psycopg2 connection/cursor interface.

Translates %s placeholders → ?, handles RETURNING id via lastrowid,
and rewrites Postgres-specific DDL to SQLite-compatible SQL.
"""
from __future__ import annotations
import re
import sqlite3
from contextlib import contextmanager

_SQLITE_PATH: str = ""


def configure(path: str):
    global _SQLITE_PATH
    _SQLITE_PATH = path


class _Cursor:
    def __init__(self, cur: sqlite3.Cursor):
        self._cur = cur
        self._last_returning: bool = False

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        return self._cur.rowcount

    def _translate(self, sql: str) -> str:
        # %s → ?
        sql = sql.replace("%s", "?")
        # SERIAL PRIMARY KEY → INTEGER PRIMARY KEY AUTOINCREMENT
        sql = re.sub(r'\bSERIAL\b', 'INTEGER', sql, flags=re.IGNORECASE)
        # NOW()::text → CURRENT_TIMESTAMP
        sql = re.sub(r"NOW\(\)::text", "CURRENT_TIMESTAMP", sql, flags=re.IGNORECASE)
        # CASE WHEN role='member' THEN ? ELSE team_name END  — fine as-is in SQLite
        # ON CONFLICT (col) DO NOTHING → OR IGNORE in INSERT
        sql = re.sub(r'\bON CONFLICT\s*\([^)]*\)\s*DO NOTHING', '', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bON CONFLICT\s+DO NOTHING', '', sql, flags=re.IGNORECASE)
        # REFERENCES … ON DELETE CASCADE — SQLite supports this if PRAGMA is set
        return sql

    def execute(self, sql: str, params=()):
        # Strip RETURNING clause; we use lastrowid instead
        self._last_returning = bool(re.search(r'\bRETURNING\b', sql, re.IGNORECASE))
        had_conflict = bool(re.search(r'\bON CONFLICT\b', sql, re.IGNORECASE))

        sql = re.sub(r'\s*RETURNING\s+\w+', '', sql, flags=re.IGNORECASE)
        translated = self._translate(sql)

        # INSERT … ON CONFLICT … DO NOTHING → INSERT OR IGNORE INTO
        if had_conflict and re.match(r'\s*INSERT\s+INTO\b', translated, re.IGNORECASE):
            translated = re.sub(r'INSERT\s+INTO', 'INSERT OR IGNORE INTO',
                                translated, count=1, flags=re.IGNORECASE)

        self._cur.execute(translated, params)

    def fetchone(self):
        if self._last_returning:
            # Simulate RETURNING id
            return (self._cur.lastrowid,)
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _Connection:
    def __init__(self, con: sqlite3.Connection):
        self._con = con

    def cursor(self) -> _Cursor:
        self._con.execute("PRAGMA foreign_keys = ON")
        return _Cursor(self._con.cursor())

    def commit(self):
        self._con.commit()

    def rollback(self):
        self._con.rollback()

    def close(self):
        self._con.close()


@contextmanager
def get_connection():
    con = sqlite3.connect(_SQLITE_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    wrapped = _Connection(con)
    try:
        yield wrapped
        wrapped.commit()
    except Exception:
        wrapped.rollback()
        raise
    finally:
        con.close()
