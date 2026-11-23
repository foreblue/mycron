"""mycron 에 등록된 작업과 상태를 브라우저로 보는 읽기 전용 웹서비스.

DB 는 읽기 전용(sqlite mode=ro)으로만 열고, 작업을 바꾸거나 실행하는 경로는 없다.
표준 라이브러리 http.server 만 쓴다.
"""

import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from apscheduler.triggers.cron import CronTrigger

from . import daemon
from . import db as database
from . import launchd
from .config import Config
from .crontext import describe
from .executor import ALERT_EXIT_CODE
from .redact import redact

logger = logging.getLogger("mycron.web")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
HEALTH_PATH = "/healthz"

# 목록 화면에서 작업마다 보는 최근 실행 수
RECENT_RUNS = 10
# 상세 화면의 실행 기록 수
DEFAULT_HISTORY = 20
MAX_HISTORY = 200

# 출력 꼬리: 가림 처리는 끝쪽 WINDOW 글자에 하고, 그중 마지막 줄들만 보여 준다
TAIL_LINES = 80
TAIL_CHARS = 8000
TAIL_WINDOW = 32_000

_TIMEOUT_MARK = "Command timed out after"

_LOG_TS = r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ "
_LOG_EXECUTING = re.compile(_LOG_TS + r"INFO mycron\.scheduler: Executing job '(.*?)': ")
_LOG_FINISHED = re.compile(
    _LOG_TS + r"INFO mycron\.scheduler: Job '(.*)' "
    r"(?:SUCCESS|ALERT|FAILED \(exit -?\d+\)) in \d+ms\s*$"
)
_LOG_DAEMON_STARTED = "mycron daemon started (PID"


class DataUnavailable(Exception):
    """DB 를 읽을 수 없을 때."""


class PortInUse(Exception):
    pass


# ──────────────────────────── 화면에 넘기는 값 ────────────────────────────


@dataclass
class OutputTail:
    text: str
    shown_lines: int
    total_lines: int

    @property
    def truncated(self) -> bool:
        return self.shown_lines < self.total_lines


@dataclass
class RunView:
    id: int
    started: datetime | None
    duration_ms: int
    exit_code: int
    kind: str  # ok / alert / timeout / fail
    notified: bool
    stdout: OutputTail | None = None
    stderr: OutputTail | None = None

    @property
    def failed(self) -> bool:
        return self.kind in ("fail", "timeout")


@dataclass
class RunningView:
    job_name: str
    started: datetime
    over_timeout: bool = False


@dataclass
class JobView:
    job: database.Job
    schedule: str
    command: str
    next_run: datetime | None
    description: str = ""
    runs: list[RunView] = field(default_factory=list)
    running: list[RunningView] = field(default_factory=list)

    @property
    def last_run(self) -> RunView | None:
        return self.runs[0] if self.runs else None

    @property
    def recent(self) -> list[RunView]:
        return self.runs[:RECENT_RUNS]

    @property
    def recent_failures(self) -> int:
        return sum(1 for run in self.recent if run.failed)

    @property
    def last_failure(self) -> RunView | None:
        return next((run for run in self.recent if run.failed), None)

    @property
    def last_failed(self) -> bool:
        return bool(self.runs) and self.runs[0].failed


@dataclass
class DaemonView:
    pid: int | None
    running: bool
    stale: bool
    started: datetime | None
    launch_agent: bool


@dataclass
class Overview:
    now: datetime
    daemon: DaemonView
    jobs: list[JobView]
    running: list[RunningView]


@dataclass
class JobDetail:
    now: datetime
    daemon: DaemonView
    view: JobView
    limit: int


# ──────────────────────────── 값 만들기 ────────────────────────────


def local_time(ts: str | None) -> datetime | None:
    """UTC 로 저장된 타임스탬프를 로컬 시각으로 바꾼다."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).astimezone()
    except ValueError:
        return None


def classify(exit_code: int, stderr_head: str | None) -> str:
    if exit_code == 0:
        return "ok"
    if exit_code == ALERT_EXIT_CODE:
        return "alert"
    if exit_code == -1 and (stderr_head or "").startswith(_TIMEOUT_MARK):
        return "timeout"
    return "fail"


def next_fire_time(cron_expr: str, now: datetime) -> datetime | None:
    """데몬과 같은 방식(APScheduler CronTrigger)으로 다음 실행 시각을 구한다."""
    try:
        return CronTrigger.from_crontab(cron_expr).get_next_fire_time(None, now)
    except Exception:
        return None


def tail_output(text: str | None) -> OutputTail | None:
    """출력의 끝부분을 가림 처리해서 돌려준다."""
    if not text:
        return None
    total_lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    window = text[-TAIL_WINDOW:]
    if len(text) > TAIL_WINDOW:
        # 잘린 첫 줄(또는 첫 낱말)은 비밀값의 조각일 수 있으니 버린다
        newline = window.find("\n")
        window = window[newline + 1 :] if newline != -1 else re.sub(r"^\S*", "", window)
    lines = redact(window).splitlines()[-TAIL_LINES:]
    while lines and len("\n".join(lines)) > TAIL_CHARS:
        if len(lines) == 1:
            lines = ["…" + re.sub(r"^\S*", "", lines[0][-TAIL_CHARS:])]
            break
        lines = lines[1:]
    return OutputTail(text="\n".join(lines), shown_lines=len(lines), total_lines=total_lines)


def parse_running(lines: list[str]) -> list[tuple[str, datetime]]:
    """데몬 로그에서 시작 기록은 있고 끝 기록이 없는 실행을 찾는다."""
    open_runs: dict[str, list[datetime]] = {}
    for line in lines:
        if "mycron." not in line:
            continue
        if _LOG_DAEMON_STARTED in line:
            # 데몬이 다시 떴으면 그 전에 돌던 실행은 더 이상 추적되지 않는다
            open_runs.clear()
            continue
        if match := _LOG_EXECUTING.match(line):
            started = _parse_log_time(match[1])
            if started:
                open_runs.setdefault(match[2], []).append(started)
        elif match := _LOG_FINISHED.match(line):
            if open_runs.get(match[2]):
                open_runs[match[2]].pop(0)
    found = [(name, started) for name, starts in open_runs.items() for started in starts]
    return sorted(found, key=lambda item: item[1])


def _parse_log_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").astimezone()
    except ValueError:
        return None


def _tail_bytes(path: Path, limit: int) -> bytes:
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - limit))
        return f.read()


class Monitor:
    """DB · PID 파일 · 데몬 로그를 읽어 화면용 값을 만든다. 아무것도 쓰지 않는다."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._running_cache: tuple[tuple, list[tuple[str, datetime]]] | None = None
        self._started_cache: tuple[int, datetime | None] | None = None

    # ── 데몬 ──

    def daemon_view(self) -> DaemonView:
        status = daemon.get_status(self.cfg)
        return DaemonView(
            pid=status.pid,
            running=status.running,
            stale=status.stale,
            started=self._process_started(status.pid) if status.running else None,
            launch_agent=launchd.PLIST_PATH.exists(),
        )

    def _process_started(self, pid: int) -> datetime | None:
        with self._lock:
            if self._started_cache and self._started_cache[0] == pid:
                return self._started_cache[1]
        started = None
        try:
            out = subprocess.run(
                ["/bin/ps", "-o", "lstart=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=3,
                env={**os.environ, "LC_ALL": "C"},
            ).stdout.strip()
            if out:
                started = datetime.strptime(out, "%a %b %d %H:%M:%S %Y").astimezone()
        except (OSError, ValueError, subprocess.SubprocessError):
            started = None
        with self._lock:
            self._started_cache = (pid, started)
        return started

    # ── 실행 중 ──

    def running_now(self, daemon_running: bool) -> list[tuple[str, datetime]]:
        if not daemon_running:
            return []
        log_path = self.cfg.daemon_log
        try:
            stat = log_path.stat()
        except OSError:
            return []
        key = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if self._running_cache and self._running_cache[0] == key:
                return self._running_cache[1]
        try:
            chunks = []
            rotated = log_path.with_name(log_path.name + ".1")
            # 방금 로그가 넘어갔으면 이전 파일 끝도 본다
            if stat.st_size < 512 * 1024 and rotated.exists():
                chunks.append(_tail_bytes(rotated, 1024 * 1024))
            chunks.append(_tail_bytes(log_path, 8 * 1024 * 1024))
        except OSError:
            return []
        lines = b"".join(chunks).decode("utf-8", errors="replace").splitlines()
        found = parse_running(lines)
        with self._lock:
            self._running_cache = (key, found)
        return found

    # ── 작업 ──

    def overview(self, now: datetime | None = None) -> Overview:
        now = now or datetime.now().astimezone()
        daemon_view = self.daemon_view()
        with self._connect() as conn:
            jobs = database.list_jobs(conn, include_disabled=True)
            summaries = database.recent_runs_by_job(conn, RECENT_RUNS)
        running = self.running_now(daemon_view.running)
        views = []
        for job in jobs:
            runs = [
                RunView(
                    id=s.id,
                    started=local_time(s.started_at),
                    duration_ms=s.duration_ms,
                    exit_code=s.exit_code,
                    kind=classify(s.exit_code, s.stderr_head),
                    notified=s.notified,
                )
                for s in summaries.get(job.id, [])
            ]
            views.append(self._job_view(job, runs, running, now))
        views.sort(key=_attention_order)
        by_name = {view.job.name: view for view in views}
        running_views = [
            _running_view(name, started, by_name.get(name), now) for name, started in running
        ]
        return Overview(now=now, daemon=daemon_view, jobs=views, running=running_views)

    def job_detail(self, name: str, limit: int = DEFAULT_HISTORY, now: datetime | None = None) -> JobDetail | None:
        now = now or datetime.now().astimezone()
        limit = max(1, min(limit, MAX_HISTORY))
        daemon_view = self.daemon_view()
        with self._connect() as conn:
            job = database.get_job(conn, name)
            if job is None:
                return None
            logs = database.get_logs(conn, job.id, limit)
        runs = [
            RunView(
                id=log.id,
                started=local_time(log.started_at),
                duration_ms=log.duration_ms,
                exit_code=log.exit_code,
                kind=classify(log.exit_code, log.stderr),
                notified=log.notified,
                stdout=tail_output(log.stdout),
                stderr=tail_output(log.stderr),
            )
            for log in logs
        ]
        running = self.running_now(daemon_view.running)
        view = self._job_view(job, runs, running, now)
        return JobDetail(now=now, daemon=daemon_view, view=view, limit=limit)

    def health(self) -> tuple[bool, dict]:
        status = daemon.get_status(self.cfg)
        info = {
            "status": "ok",
            "db": "ok",
            "daemon": "running" if status.running else "stopped",
        }
        try:
            with self._connect() as conn:
                info["jobs"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        except DataUnavailable as e:
            info.update(status="error", db=str(e))
            return False, info
        return True, info

    def _job_view(
        self,
        job: database.Job,
        runs: list[RunView],
        running: list[tuple[str, datetime]],
        now: datetime,
    ) -> JobView:
        view = JobView(
            job=job,
            schedule=describe(job.cron_expr),
            command=redact(job.command),
            next_run=next_fire_time(job.cron_expr, now) if job.enabled else None,
            description=redact(job.description),
            runs=runs,
        )
        view.running = [
            _running_view(name, started, view, now) for name, started in running if name == job.name
        ]
        return view

    def _connect(self) -> "_ReadOnly":
        return _ReadOnly(self.cfg.db_path)


class _ReadOnly:
    """요청마다 읽기 전용 연결을 열고 닫는다. sqlite 오류는 DataUnavailable 로 바꾼다."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        try:
            self.conn = database.connect_readonly(self.db_path)
        except sqlite3.Error as e:
            raise DataUnavailable(f"DB 를 열 수 없습니다: {self.db_path} ({e})") from e
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn is not None:
            self.conn.close()
        if exc_type is not None and issubclass(exc_type, sqlite3.Error):
            raise DataUnavailable(f"DB 를 읽을 수 없습니다: {exc}") from exc


def _running_view(name: str, started: datetime, view: JobView | None, now: datetime) -> RunningView:
    over = False
    if view is not None:
        over = (now - started).total_seconds() > view.job.timeout_seconds + 60
    return RunningView(job_name=name, started=started, over_timeout=over)


def _attention_order(view: JobView) -> tuple:
    """꺼진 작업은 뒤로, 마지막 실행이 실패한 작업은 앞으로, 나머지는 다음 실행이 가까운 순."""
    far = float("inf") if view.next_run is None else view.next_run.timestamp()
    return (not view.job.enabled, not view.last_failed, far, view.job.name)


# ──────────────────────────── JSON ────────────────────────────


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _run_dict(run: RunView) -> dict:
    return {
        "started_at": _iso(run.started),
        "duration_ms": run.duration_ms,
        "exit_code": run.exit_code,
        "result": run.kind,
    }


def overview_dict(overview: Overview) -> dict:
    return {
        "generated_at": _iso(overview.now),
        "daemon": {
            "running": overview.daemon.running,
            "pid": overview.daemon.pid,
            "stale_pid_file": overview.daemon.stale,
            "started_at": _iso(overview.daemon.started),
            "launch_agent": overview.daemon.launch_agent,
        },
        "running": [
            {"name": r.job_name, "started_at": _iso(r.started), "over_timeout": r.over_timeout}
            for r in overview.running
        ],
        "jobs": [
            {
                "name": v.job.name,
                "description": v.description,
                "cron": v.job.cron_expr,
                "schedule": v.schedule,
                "enabled": v.job.enabled,
                "skip_if_running": v.job.skip_if_running,
                "notify_on_success": v.job.notify_on_success,
                "timeout_seconds": v.job.timeout_seconds,
                "command": v.command,
                "next_run_at": _iso(v.next_run),
                "running": bool(v.running),
                "last_run": _run_dict(v.last_run) if v.last_run else None,
                "recent_failures": v.recent_failures,
                "recent_runs": len(v.recent),
            }
            for v in overview.jobs
        ],
    }


# ──────────────────────────── HTTP ────────────────────────────


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


# 게이트웨이(default-src 'self' + 인라인 허용)와 같은 수준. 바깥 자원은 하나도 쓰지 않는다.
_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'; "
    "object-src 'none'; form-action 'none'"
)

_COMMON_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class WebApp:
    """요청 하나를 응답 하나로 바꾼다. GET/HEAD 만 받는다."""

    def __init__(self, cfg: Config, allow_hosts: tuple[str, ...] = ()):
        self.monitor = Monitor(cfg)
        self.allow_hosts = {h.strip().lower() for h in allow_hosts if h.strip()}

    def handle(self, method: str, target: str, host_header: str | None) -> Response:
        from . import webui

        try:
            if not self._host_allowed(host_header):
                return _text(
                    HTTPStatus.FORBIDDEN,
                    f"허용되지 않은 Host 입니다: {host_header}\n"
                    "게이트웨이 이름으로 접속하려면 `mycron web --allow-host <이름>` 으로 허용하세요.\n",
                )
            if method not in ("GET", "HEAD"):
                response = _text(HTTPStatus.METHOD_NOT_ALLOWED, "읽기 전용 서비스입니다 (GET 만 됩니다).\n")
                response.headers["Allow"] = "GET, HEAD"
                return response

            parts = urlsplit(target)
            path = parts.path or "/"
            query = parse_qs(parts.query)

            if path == "/":
                return _html(HTTPStatus.OK, webui.render_overview(self.monitor.overview()))
            if path == HEALTH_PATH:
                healthy, info = self.monitor.health()
                status = HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE
                return _json(status, info)
            if path == "/api/status":
                return _json(HTTPStatus.OK, overview_dict(self.monitor.overview()))
            if path.startswith("/jobs/"):
                raw_name = path[len("/jobs/") :]
                if raw_name and "/" not in raw_name:
                    detail = self.monitor.job_detail(unquote(raw_name), _history_limit(query))
                    if detail is not None:
                        return _html(HTTPStatus.OK, webui.render_job(detail))
            return _html(
                HTTPStatus.NOT_FOUND,
                webui.render_message("찾을 수 없습니다", "주소가 틀렸거나 삭제된 작업입니다.", _home_link(path)),
            )
        except DataUnavailable as e:
            if urlsplit(target).path.startswith("/api/"):
                return _json(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "db": str(e)})
            return _html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                webui.render_message("mycron DB 를 읽을 수 없습니다", str(e), _home_link(urlsplit(target).path)),
            )
        except Exception:
            logger.exception("요청 처리 실패: %s %s", method, target)
            return _text(HTTPStatus.INTERNAL_SERVER_ERROR, "내부 오류가 났습니다. 서버 로그를 확인하세요.\n")

    def _host_allowed(self, host_header: str | None) -> bool:
        """DNS 리바인딩으로 다른 사이트가 이 서비스를 읽어 가는 것을 막는다.

        IP 주소 · localhost 는 항상 받고, 그 밖의 이름은 --allow-host 로 허용한 것만 받는다.
        """
        if not host_header or "*" in self.allow_hosts:
            return True
        name = _strip_port(host_header).lower()
        if name == "localhost" or name in self.allow_hosts:
            return True
        try:
            ipaddress.ip_address(name)
            return True
        except ValueError:
            return False


def _strip_port(host_header: str) -> str:
    host = host_header.strip()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


def _history_limit(query: dict[str, list[str]]) -> int:
    try:
        return int(query.get("n", [DEFAULT_HISTORY])[0])
    except (TypeError, ValueError):
        return DEFAULT_HISTORY


def _home_link(path: str) -> str:
    """현재 경로에서 목록으로 가는 상대 주소 (게이트웨이가 앞에 경로를 붙여도 맞게)."""
    depth = max(0, path.count("/") - 1)
    return "../" * depth or "./"


def _html(status: HTTPStatus, body: str) -> Response:
    return Response(int(status), body.encode("utf-8"))


def _text(status: HTTPStatus, body: str) -> Response:
    return Response(int(status), body.encode("utf-8"), "text/plain; charset=utf-8")


def _json(status: HTTPStatus, payload: dict) -> Response:
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return Response(int(status), body.encode("utf-8"), "application/json; charset=utf-8")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    def version_string(self) -> str:
        return "mycron-web"

    def _dispatch(self) -> None:
        response = self.server.app.handle(self.command, self.path, self.headers.get("Host"))
        if self.command not in ("GET", "HEAD"):
            # 읽지 않은 요청 본문이 다음 요청으로 넘어가지 않게 연결을 닫는다
            self.close_connection = True
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        for name, value in {**_COMMON_HEADERS, **response.headers}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _dispatch

    def log_message(self, format: str, *args) -> None:
        logger.info("%s %s", self.address_string(), format % args)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    app: WebApp


class _ServerV6(_Server):
    address_family = socket.AF_INET6


def create_server(cfg: Config, host: str, port: int, allow_hosts: tuple[str, ...] = ()) -> _Server:
    server_class = _ServerV6 if ":" in host else _Server
    server = server_class((host, port), _Handler)
    server.app = WebApp(cfg, allow_hosts)
    return server


def port_in_use(host: str, port: int) -> bool:
    """이미 그 포트에서 듣는 프로세스가 있는지 접속해 본다."""
    probe_host = {"0.0.0.0": "127.0.0.1", "": "127.0.0.1", "::": "::1"}.get(host, host)
    try:
        with socket.create_connection((probe_host, port), timeout=0.5):
            return True
    except OSError:
        return False


def serve(
    cfg: Config,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allow_hosts: tuple[str, ...] = (),
    verbose: bool = False,
) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if port_in_use(host, port):
        raise PortInUse(f"{host}:{port} 는 이미 다른 프로세스가 쓰고 있습니다.")
    try:
        server = create_server(cfg, host, port, allow_hosts)
    except OSError as e:
        raise PortInUse(f"{host}:{port} 에 바인드할 수 없습니다: {e}") from e

    shown_host = f"[{host}]" if ":" in host else host
    print(f"mycron web (읽기 전용): http://{shown_host}:{server.server_address[1]}/", flush=True)
    print(f"DB: {cfg.db_path} · 헬스 체크: {HEALTH_PATH} · 종료: Ctrl+C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
