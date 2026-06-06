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


if __name__ == "__main__":
    unittest.main()
