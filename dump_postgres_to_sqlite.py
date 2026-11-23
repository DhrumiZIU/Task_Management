"""Dump all production Postgres data into local team_planner.db (SQLite).

Run once from the project root:
    python3 dump_postgres_to_sqlite.py

Safe to re-run — clears each table before inserting.
"""
import os, re, sqlite3, sys
import psycopg2, psycopg2.extras

# ── Load DATABASE_URL ─────────────────────────────────────────────────────────
env_text = open(os.path.join(os.path.dirname(__file__), ".env")).read()
match = re.search(r"DATABASE_URL=(.+)", env_text)
if not match:
    sys.exit("DATABASE_URL not found in .env")
pg_url = match.group(1).strip()

# ── Connect ───────────────────────────────────────────────────────────────────
print("Connecting to Postgres …")
pg = psycopg2.connect(pg_url)
pgc = pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

sq = sqlite3.connect(os.path.join(os.path.dirname(__file__), "team_planner.db"))
sq.execute("PRAGMA foreign_keys = OFF")

TABLES = ["users", "tasks", "discussion", "activity_log",
          "projects", "project_members", "meetings"]

for table in TABLES:
    try:
        pgc.execute(f"SELECT * FROM {table}")
        rows = pgc.fetchall()
    except psycopg2.errors.UndefinedTable:
        pg.rollback()
        print(f"  {table}: not found in Postgres, skipping")
        continue

    if not rows:
        print(f"  {table}: 0 rows, skipping")
        continue

    cols = list(rows[0].keys())
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)

    sq.execute(f"DELETE FROM {table}")
    sq.executemany(
        f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
        [tuple(r[c] for c in cols) for r in rows],
    )
    sq.commit()
    print(f"  {table}: {len(rows)} rows copied")

pg.close()
sq.close()
print("\nDone — team_planner.db is now in sync with production.")
