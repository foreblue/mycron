import sqlite3
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
        return _config(tmpdir)

    def _patched_runtime(self, cfg: Config):
        return _patched_runtime(cfg)


class JobDescriptionCliTest(unittest.TestCase):
    def test_add_with_description(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cfg = _config(tmpdir)
            runner = CliRunner()

            with _patched_runtime(cfg):
                result = runner.invoke(
                    main,
                    ["add", "--name", "inbox", "--cron", "*/15 * * * *", "--command", "true",
                     "--description", "요청 이슈를 분류해 답함"],
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("설명: 요청 이슈를 분류해 답함", result.output)

                result = runner.invoke(main, ["add", "--name", "plain", "--cron", "* * * * *", "--command", "true"])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertNotIn("설명", result.output)

            conn = database.connect(cfg.db_path)
            self.assertEqual(database.get_job(conn, "inbox").description, "요청 이슈를 분류해 답함")
            self.assertEqual(database.get_job(conn, "plain").description, "")

    def test_describe_sets_shows_and_clears(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cfg = _config(tmpdir)
            conn = database.connect(cfg.db_path)
            database.init_db(conn)
            database.add_job(conn, name="guard", cron_expr="0 */6 * * *", command="true")
            runner = CliRunner()

            with _patched_runtime(cfg):
                result = runner.invoke(main, ["describe", "guard"])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(result.output, "\n")

                result = runner.invoke(main, ["describe", "guard", "디스크가 차면\n텔레그램 경고"])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("설명 설정됨: guard — 디스크가 차면 텔레그램 경고", result.output)

                result = runner.invoke(main, ["describe", "guard"])
                self.assertEqual(result.output, "디스크가 차면 텔레그램 경고\n")

                result = runner.invoke(main, ["describe", "guard", ""])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("설명 지움: guard", result.output)

            self.assertEqual(database.get_job(conn, "guard").description, "")

    def test_describe_unknown_job_fails(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cfg = _config(tmpdir)
            runner = CliRunner()

            with _patched_runtime(cfg):
                result = runner.invoke(main, ["describe", "nope", "설명"])

            self.assertEqual(result.exit_code, 1)
            self.assertIn("'nope' 작업을 찾을 수 없습니다", result.output)

    def test_describe_rejects_too_long_text(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cfg = _config(tmpdir)
            conn = database.connect(cfg.db_path)
            database.init_db(conn)
            database.add_job(conn, name="guard", cron_expr="0 */6 * * *", command="true", description="원래 설명")
            runner = CliRunner()

            with _patched_runtime(cfg):
                result = runner.invoke(main, ["describe", "guard", "가" * 201])

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("200자까지", result.output)
            self.assertEqual(database.get_job(conn, "guard").description, "원래 설명")

    def test_describe_does_not_reload_the_daemon(self) -> None:
        """설명은 스케줄과 무관하다: 데몬에 신호를 보내지 않는다."""
        with TemporaryDirectory() as tmpdir:
            cfg = _config(tmpdir)
            conn = database.connect(cfg.db_path)
            database.init_db(conn)
            database.add_job(conn, name="guard", cron_expr="0 */6 * * *", command="true")
            fake_daemon = Mock()

            with patch.multiple("mycron.cli", load_config=lambda: cfg, daemon=fake_daemon):
                result = CliRunner().invoke(main, ["describe", "guard", "설명"])

            self.assertEqual(result.exit_code, 0, result.output)
            fake_daemon.signal_reload.assert_not_called()

    def test_old_db_is_migrated_by_describe(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cfg = _config(tmpdir)
            old = sqlite3.connect(cfg.db_path)
            old.executescript("""
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
                INSERT INTO jobs (name, cron_expr, command) VALUES ('legacy', '0 3 * * *', '/bin/true');
            """)
            old.commit()
            old.close()

            with _patched_runtime(cfg):
                result = CliRunner().invoke(main, ["describe", "legacy", "옛 작업"])

            self.assertEqual(result.exit_code, 0, result.output)
            job = database.get_job(database.connect(cfg.db_path), "legacy")
            self.assertEqual((job.description, job.cron_expr, job.command), ("옛 작업", "0 3 * * *", "/bin/true"))


def _config(tmpdir: str) -> Config:
    root = Path(tmpdir)
    return Config(
        db_path=root / "mycron.db",
        pid_file=root / "mycron.pid",
        daemon_log=root / "daemon.log",
    )


def _patched_runtime(cfg: Config):
    return patch.multiple(
        "mycron.cli",
        load_config=lambda: cfg,
        daemon=Mock(signal_reload=lambda _cfg: None),
    )


if __name__ == "__main__":
    unittest.main()
