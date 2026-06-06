import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mycron import db as database
from mycron.cli import main
from mycron.config import Config
from mycron.executor import ExecutionResult


class JobTimeoutCliTest(unittest.TestCase):
    def test_add_list_and_set_timeout(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cfg = self._config(tmpdir)
            runner = CliRunner()

            with self._patched_runtime(cfg):
                result = runner.invoke(
                    main,
                    [
                        "add",
                        "--name",
                        "slow",
                        "--cron",
                        "* * * * *",
                        "--command",
                        "sleep 10",
                        "--timeout",
                        "12.5",
                    ],
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("timeout=12.5s", result.output)

                result = runner.invoke(
                    main,
                    ["set-timeout", "slow", "--timeout", "7"],
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("Timeout 설정됨: slow (7s)", result.output)

                result = runner.invoke(main, ["list", "--all"])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("timeout=7s", result.output)

            conn = database.connect(cfg.db_path)
            database.init_db(conn)
            self.assertEqual(database.get_job(conn, "slow").timeout_seconds, 7)

    def test_run_passes_job_timeout_to_executor(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cfg = self._config(tmpdir)
            conn = database.connect(cfg.db_path)
            database.init_db(conn)
            database.add_job(
                conn,
                name="slow",
                cron_expr="* * * * *",
                command="sleep 10",
                timeout_seconds=2.5,
            )

            runner = CliRunner()
            execution_result = ExecutionResult(
                started_at="2026-01-01T00:00:00",
                finished_at="2026-01-01T00:00:01",
                duration_ms=1000,
                exit_code=0,
                stdout="done",
                stderr=None,
            )

            with self._patched_runtime(cfg), patch(
                "mycron.cli.run_command", return_value=execution_result
            ) as run_command, patch("mycron.cli.notify", return_value=False):
                result = runner.invoke(main, ["run", "slow"])

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("timeout: 2.5s", result.output)
            run_command.assert_called_once_with("sleep 10", timeout=2.5)

    def _config(self, tmpdir: str) -> Config:
        root = Path(tmpdir)
        return Config(
            db_path=root / "mycron.db",
            pid_file=root / "mycron.pid",
            daemon_log=root / "daemon.log",
        )

    def _patched_runtime(self, cfg: Config):
        return patch.multiple(
            "mycron.cli",
            load_config=lambda: cfg,
            daemon=Mock(signal_reload=lambda _cfg: None),
        )


if __name__ == "__main__":
    unittest.main()
