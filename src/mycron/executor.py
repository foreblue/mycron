import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB


@dataclass
class ExecutionResult:
    started_at: str
    finished_at: str
    duration_ms: int
    exit_code: int
    stdout: str | None
    stderr: str | None

    @property
    def success(self) -> bool:
        return self.exit_code == 0


def run_command(command: str) -> ExecutionResult:
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    t0 = time.monotonic()

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        exit_code = result.returncode
        stdout = _truncate(result.stdout)
        stderr = _truncate(result.stderr)
    except subprocess.TimeoutExpired:
        exit_code = -1
        stdout = None
        stderr = "Command timed out after 3600 seconds"
    except Exception as e:
        exit_code = -1
        stdout = None
        stderr = str(e)

    duration_ms = int((time.monotonic() - t0) * 1000)
    finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    return ExecutionResult(
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        exit_code=exit_code,
        stdout=stdout or None,
        stderr=stderr or None,
    )


def _truncate(text: str) -> str:
    if len(text.encode()) > MAX_OUTPUT_BYTES:
        truncated = text.encode()[:MAX_OUTPUT_BYTES].decode(errors="replace")
        return truncated + "\n... [truncated]"
    return text
