import shlex
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mycron.executor import run_command


class RunCommandTest(unittest.TestCase):
    def test_run_command_returns_success_output(self) -> None:
        result = run_command("printf hello", timeout=1)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "hello")
        self.assertIsNone(result.stderr)

    def test_timeout_terminates_background_child_process(self) -> None:
        with TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "child-survived"
            child_code = (
                "import pathlib, sys, time; "
                "time.sleep(1); "
                "pathlib.Path(sys.argv[1]).write_text('alive')"
            )
            command = (
                f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)} "
                f"{shlex.quote(str(marker))} & wait"
            )

            result = run_command(command, timeout=0.1)
            time.sleep(1.2)

            self.assertEqual(result.exit_code, -1)
            self.assertIn("Command timed out after 0.1 seconds", result.stderr or "")
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
