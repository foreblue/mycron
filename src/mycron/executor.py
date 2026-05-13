import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB
DEFAULT_TIMEOUT_SECONDS = 3600
TERMINATE_GRACE_SECONDS = 5


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


def run_command(command: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> ExecutionResult:
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    t0 = time.monotonic()
    process: subprocess.Popen[str] | None = None

    try:
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout_text, stderr_text = process.communicate(timeout=timeout)
        exit_code = process.returncode
        stdout = _truncate(stdout_text)
        stderr = _truncate(stderr_text)
    except subprocess.TimeoutExpired:
        stdout_text = ""
        stderr_text = ""
        if process is not None:
            stdout_text, stderr_text = _terminate_process_group(process)
        exit_code = -1
        stdout = _truncate(stdout_text)
        stderr_parts = [f"Command timed out after {_format_timeout(timeout)} seconds"]
        if stderr_text:
            stderr_parts.append(_truncate(stderr_text))
        stderr = "\n".join(stderr_parts)
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


def _terminate_process_group(process: subprocess.Popen[str]) -> tuple[str, str]:
    _signal_process_group(process, signal.SIGTERM)
    try:
        return process.communicate(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)
        return process.communicate()


def _signal_process_group(process: subprocess.Popen[str], sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return


def _format_timeout(timeout: float) -> str:
    if float(timeout).is_integer():
        return str(int(timeout))
    return str(timeout)
