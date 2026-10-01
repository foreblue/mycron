import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mycron.executor import ALERT_EXIT_CODE, ExecutionResult
from mycron.notifier import _build_message


def _result(exit_code: int, stdout: str | None = None, stderr: str | None = None) -> ExecutionResult:
    return ExecutionResult(
        started_at="2026-10-01T00:00:00",
        finished_at="2026-10-01T00:00:01",
        duration_ms=1000,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


class BuildMessageTest(unittest.TestCase):
    def test_alert_exit_code_shows_alert_with_stdout(self) -> None:
        message = _build_message("disk-guard", _result(ALERT_EXIT_CODE, stdout="디스크 여유 13G"))

        self.assertIn('Job "disk-guard" ALERT', message)
        self.assertIn("디스크 여유 13G", message)
        self.assertNotIn("FAILED", message)

    def test_failure_without_stderr_includes_stdout_tail(self) -> None:
        message = _build_message("job", _result(1, stdout="x" * 600 + "END"))

        self.assertIn("FAILED (exit 1)", message)
        self.assertIn("Stdout: ", message)
        self.assertTrue(message.endswith("END"))

    def test_failure_with_stderr_omits_stdout(self) -> None:
        message = _build_message("job", _result(2, stdout="noise", stderr="boom"))

        self.assertIn("Stderr: boom", message)
        self.assertNotIn("noise", message)


if __name__ == "__main__":
    unittest.main()
