import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Job:
    id: int
    name: str
    cron_expr: str
    command: str
    enabled: bool
    skip_if_running: bool
    created_at: str
    updated_at: str


@dataclass
class Log:
    id: int
    job_id: int
    job_name: str
    started_at: str
    finished_at: str
    duration_ms: int
    exit_code: int
    stdout: str | None
    stderr: str | None
    notified: bool


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT    NOT NULL UNIQUE,
            cron_expr        TEXT    NOT NULL,
            command          TEXT    NOT NULL,
            enabled          INTEGER NOT NULL DEFAULT 1,
            skip_if_running  INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );

        CREATE TABLE IF NOT EXISTS logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            started_at  TEXT    NOT NULL,
            finished_at TEXT    NOT NULL,
            duration_ms INTEGER NOT NULL,
            exit_code   INTEGER NOT NULL,
            stdout      TEXT,
            stderr      TEXT,
            notified    INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_logs_job_id     ON logs(job_id);
        CREATE INDEX IF NOT EXISTS idx_logs_started_at ON logs(started_at);
    """)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "skip_if_running" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN skip_if_running INTEGER NOT NULL DEFAULT 0")


def add_job(
    conn: sqlite3.Connection,
    name: str,
    cron_expr: str,
    command: str,
    skip_if_running: bool = False,
) -> Job:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO jobs (name, cron_expr, command, skip_if_running, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, cron_expr, command, 1 if skip_if_running else 0, now, now),
    )
    conn.commit()
    return get_job(conn, name)


def remove_job(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("DELETE FROM jobs WHERE name = ?", (name,))
    conn.commit()
    return cur.rowcount > 0


def get_job(conn: sqlite3.Connection, name: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE name = ?", (name,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(conn: sqlite3.Connection, include_disabled: bool = False) -> list[Job]:
    if include_disabled:
        rows = conn.execute("SELECT * FROM jobs ORDER BY name").fetchall()
    else:
        rows = conn.execute("SELECT * FROM jobs WHERE enabled = 1 ORDER BY name").fetchall()
    return [_row_to_job(r) for r in rows]


def set_job_enabled(conn: sqlite3.Connection, name: str, enabled: bool) -> bool:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.execute(
        "UPDATE jobs SET enabled = ?, updated_at = ? WHERE name = ?",
        (1 if enabled else 0, now, name),
    )
    conn.commit()
    return cur.rowcount > 0


def insert_log(
    conn: sqlite3.Connection,
    job_id: int,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    exit_code: int,
    stdout: str | None,
    stderr: str | None,
) -> int:
    cur = conn.execute(
        """INSERT INTO logs
           (job_id, started_at, finished_at, duration_ms, exit_code, stdout, stderr)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (job_id, started_at, finished_at, duration_ms, exit_code, stdout, stderr),
    )
    conn.commit()
    return cur.lastrowid


def mark_log_notified(conn: sqlite3.Connection, log_id: int) -> None:
    conn.execute("UPDATE logs SET notified = 1 WHERE id = ?", (log_id,))
    conn.commit()


def get_logs(conn: sqlite3.Connection, job_id: int | None = None, limit: int = 20) -> list[Log]:
    if job_id is not None:
        rows = conn.execute(
            """SELECT l.*, j.name as job_name
               FROM logs l JOIN jobs j ON l.job_id = j.id
               WHERE l.job_id = ?
               ORDER BY l.started_at DESC
               LIMIT ?""",
            (job_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT l.*, j.name as job_name
               FROM logs l JOIN jobs j ON l.job_id = j.id
               ORDER BY l.started_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [_row_to_log(r) for r in rows]


def prune_logs(conn: sqlite3.Connection, retention_days: int) -> int:
    cur = conn.execute(
        """DELETE FROM logs
           WHERE started_at < strftime('%Y-%m-%dT%H:%M:%S', 'now', ? || ' days')""",
        (f"-{retention_days}",),
    )
    conn.commit()
    return cur.rowcount


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        name=row["name"],
        cron_expr=row["cron_expr"],
        command=row["command"],
        enabled=bool(row["enabled"]),
        skip_if_running=bool(row["skip_if_running"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_log(row: sqlite3.Row) -> Log:
    return Log(
        id=row["id"],
        job_id=row["job_id"],
        job_name=row["job_name"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        duration_ms=row["duration_ms"],
        exit_code=row["exit_code"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        notified=bool(row["notified"]),
    )
