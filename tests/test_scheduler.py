import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mycron import db as database
from mycron.config import Config
from mycron.executor import ExecutionResult
from mycron.scheduler import _execute_job


class JobTimeoutSchedulerTest(unittest.TestCase):
    def test_execute_job_passes_timeout_to_executor(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cfg = Config(
                db_path=Path(tmpdir) / "mycron.db",
                pid_file=Path(tmpdir) / "mycron.pid",
                daemon_log=Path(tmpdir) / "daemon.log",
            )
            conn = database.connect(cfg.db_path)
            database.init_db(conn)
            job = database.add_job(
                conn,
                name="slow",
                cron_expr="* * * * *",
                command="sleep 10",
                timeout_seconds=3.5,
            )
            execution_result = ExecutionResult(
                started_at="2026-01-01T00:00:00",
                finished_at="2026-01-01T00:00:01",
                duration_ms=1000,
                exit_code=0,
                stdout=None,
                stderr=None,
            )

            with patch(
                "mycron.scheduler.run_command", return_value=execution_result
            ) as run_command, patch("mycron.scheduler.send", return_value=False):
                _execute_job(
                    cfg,
                    conn,
                    job.id,
                    job.name,
                    job.command,
                    job.notify_on_success,
                    job.timeout_seconds,
                )

            run_command.assert_called_once_with("sleep 10", timeout=3.5)


if __name__ == "__main__":
    unittest.main()
