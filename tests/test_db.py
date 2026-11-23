import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mycron import db as database
from mycron.executor import DEFAULT_TIMEOUT_SECONDS


class JobTimeoutDatabaseTest(unittest.TestCase):
    def test_add_job_stores_custom_timeout(self) -> None:
        with TemporaryDirectory() as tmpdir:
            conn = database.connect(Path(tmpdir) / "mycron.db")
            database.init_db(conn)

            job = database.add_job(
                conn,
                name="slow",
                cron_expr="* * * * *",
                command="sleep 10",
                timeout_seconds=12.5,
            )

            self.assertEqual(job.timeout_seconds, 12.5)
            self.assertEqual(database.get_job(conn, "slow").timeout_seconds, 12.5)

    def test_migration_sets_default_timeout_for_existing_jobs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            conn = database.connect(Path(tmpdir) / "mycron.db")
            conn.executescript("""
                CREATE TABLE jobs (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    name               TEXT    NOT NULL UNIQUE,
                    cron_expr          TEXT    NOT NULL,
                    command            TEXT    NOT NULL,
                    enabled            INTEGER NOT NULL DEFAULT 1,
                    skip_if_running    INTEGER NOT NULL DEFAULT 0,
                    notify_on_success  INTEGER NOT NULL DEFAULT 1,
                    created_at         TEXT    NOT NULL,
                    updated_at         TEXT    NOT NULL
                );
                INSERT INTO jobs
                    (name, cron_expr, command, created_at, updated_at)
                VALUES
                    ('legacy', '* * * * *', 'printf ok', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
            """)
            conn.commit()

            database.init_db(conn)

            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            self.assertIn("timeout_seconds", cols)
            self.assertEqual(
                database.get_job(conn, "legacy").timeout_seconds,
                DEFAULT_TIMEOUT_SECONDS,
            )


class JobDescriptionDatabaseTest(unittest.TestCase):
    def test_new_job_has_empty_description_by_default(self) -> None:
        with TemporaryDirectory() as tmpdir:
            conn = database.connect(Path(tmpdir) / "mycron.db")
            database.init_db(conn)

            job = database.add_job(conn, name="plain", cron_expr="* * * * *", command="true")

            self.assertEqual(job.description, "")

    def test_add_and_set_description(self) -> None:
        with TemporaryDirectory() as tmpdir:
            conn = database.connect(Path(tmpdir) / "mycron.db")
            database.init_db(conn)

            job = database.add_job(
                conn,
                name="inbox",
                cron_expr="*/15 * * * *",
                command="true",
                description="  요청 이슈를\n분류해   답함 ",
            )
            # 한 줄로 줄여 저장한다
            self.assertEqual(job.description, "요청 이슈를 분류해 답함")

            self.assertTrue(database.set_job_description(conn, "inbox", "새 설명"))
            self.assertEqual(database.get_job(conn, "inbox").description, "새 설명")
            self.assertTrue(database.set_job_description(conn, "inbox", ""))
            self.assertEqual(database.get_job(conn, "inbox").description, "")
            self.assertFalse(database.set_job_description(conn, "nope", "x"))

    def test_set_description_leaves_schedule_fields_alone(self) -> None:
        with TemporaryDirectory() as tmpdir:
            conn = database.connect(Path(tmpdir) / "mycron.db")
            database.init_db(conn)
            before = database.add_job(
                conn,
                name="job",
                cron_expr="5 4 * * *",
                command="echo hi",
                skip_if_running=True,
                notify_on_success=False,
                timeout_seconds=30,
            )

            database.set_job_description(conn, "job", "설명")

            after = database.get_job(conn, "job")
            for field in ("id", "cron_expr", "command", "enabled", "skip_if_running", "notify_on_success", "timeout_seconds"):
                self.assertEqual(getattr(after, field), getattr(before, field), field)

    def test_migration_adds_empty_description_to_existing_jobs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "mycron.db"
            _create_db_without_description(path)
            conn = database.connect(path)

            database.init_db(conn)
            database.init_db(conn)  # 두 번 돌려도 된다

            cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            self.assertIn("description", cols)
            legacy = database.get_job(conn, "legacy")
            self.assertEqual(legacy.description, "")
            self.assertEqual(legacy.command, "printf ok")
            self.assertEqual(legacy.timeout_seconds, 120)
            self.assertEqual(len(database.get_logs(conn, legacy.id)), 1)

            database.set_job_description(conn, "legacy", "옛 작업")
            self.assertEqual(database.get_job(conn, "legacy").description, "옛 작업")

    def test_readonly_reader_works_before_migration(self) -> None:
        """mycron web 은 읽기 전용이라 마이그레이션을 못 한다. 옛 DB 도 읽을 수 있어야 한다."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "mycron.db"
            _create_db_without_description(path)

            conn = database.connect_readonly(path)
            try:
                jobs = database.list_jobs(conn, include_disabled=True)
            finally:
                conn.close()

            self.assertEqual([(job.name, job.description) for job in jobs], [("legacy", "")])


def _create_db_without_description(path: Path) -> None:
    """description 컬럼이 생기기 전(2026-11)의 jobs 테이블."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE jobs (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            name               TEXT    NOT NULL UNIQUE,
            cron_expr          TEXT    NOT NULL,
            command            TEXT    NOT NULL,
            enabled            INTEGER NOT NULL DEFAULT 1,
            created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            skip_if_running    INTEGER NOT NULL DEFAULT 0,
            notify_on_success  INTEGER NOT NULL DEFAULT 1,
            timeout_seconds    REAL    NOT NULL DEFAULT 3600
        );
        CREATE TABLE logs (
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
        INSERT INTO jobs (name, cron_expr, command, timeout_seconds)
        VALUES ('legacy', '* * * * *', 'printf ok', 120);
        INSERT INTO logs (job_id, started_at, finished_at, duration_ms, exit_code, stdout)
        VALUES (1, '2026-11-01T00:00:00', '2026-11-01T00:00:01', 1000, 0, 'ok');
    """)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    unittest.main()
