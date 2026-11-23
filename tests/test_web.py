"""mycron web: 페이지 응답 · 가림 처리 · 읽기 전용을 DB 복사본으로 확인한다."""

import hashlib
import http.client
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mycron import db as database
from mycron import launchd, web, webui
from mycron.cli import main
from mycron.config import MYCRON_DIR, Config

# 화면 어디에도 나오면 안 되는 값들
SECRET_COMMAND = "s3cr3tCmdValue99"
SECRET_BEARER = "tok-ABCDEF123456"
SECRET_STDOUT = "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SECRET_STDERR = "0123456789abcdef0123456789abcdef01234567"
SECRETS = (SECRET_COMMAND, SECRET_BEARER, SECRET_STDOUT, SECRET_STDERR)

SPECIAL_NAME = "백업 a/b"
SPECIAL_PATH = "/jobs/%EB%B0%B1%EC%97%85%20a%2Fb"


def _utc(minutes_ago: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _log(conn, job, minutes_ago, exit_code=0, stdout=None, stderr=None, duration_ms=1200):
    database.insert_log(
        conn,
        job_id=job.id,
        started_at=_utc(minutes_ago),
        finished_at=_utc(minutes_ago - duration_ms / 60000),
        duration_ms=duration_ms,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def _seed(path: Path) -> None:
    conn = database.connect(path)
    database.init_db(conn)

    report = database.add_job(
        conn,
        "daily-report",
        "30 8 * * *",
        "/opt/homebrew/bin/python3 /Users/me/report.py",
        skip_if_running=True,
        notify_on_success=False,
        timeout_seconds=300,
    )
    for minutes in (3000, 1500, 60):
        _log(conn, report, minutes, stdout="report done\n")

    flaky = database.add_job(
        conn,
        "flaky-sync",
        "*/15 * * * *",
        f'curl -s -H "Authorization: Bearer {SECRET_BEARER}" https://example.com/sync?mode=full '
        f"&& API_KEY={SECRET_COMMAND} /usr/local/bin/sync --verbose --retries 3",
    )
    _log(conn, flaky, 45, stdout="synced 10 items\n")
    _log(conn, flaky, 30, stdout="synced 12 items\n")
    _log(
        conn,
        flaky,
        15,
        exit_code=1,
        stdout=f"using token {SECRET_STDOUT}\n<script>alert(1)</script>\n",
        stderr=f"fatal: upload failed, digest {SECRET_STDERR}\n",
    )

    guard = database.add_job(conn, "disk-guard", "0 */6 * * *", "/bin/bash /Users/me/disk-guard.sh")
    _log(conn, guard, 120, exit_code=10, stdout="디스크 92% 사용\n")

    slow = database.add_job(conn, "slow-batch", "0 3 * * sun", "/bin/sleep 999", timeout_seconds=5)
    _log(conn, slow, 10000, exit_code=-1, stderr="Command timed out after 5 seconds")

    database.add_job(conn, "old-job", "0 0 1 * *", "/bin/true")
    database.set_job_enabled(conn, "old-job", False)

    special = database.add_job(conn, SPECIAL_NAME, "0 10,14 * * mon-fri", "/bin/echo backup")
    _log(conn, special, 200, stdout="".join(f"line-{n:03d}\n" for n in range(1, 301)))

    conn.close()


def _daemon_log(path: Path) -> None:
    def stamp(minutes_ago: float) -> str:
        return (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")

    path.write_text(
        "\n".join(
            [
                f"{stamp(9000)},000 INFO mycron.daemon: mycron daemon started (PID 1)",
                f"{stamp(60)},001 INFO mycron.scheduler: Executing job 'daily-report': /opt/homebrew/bin/python3",
                f"{stamp(59)},900 INFO mycron.scheduler: Job 'daily-report' SUCCESS in 1200ms",
                f"{stamp(15)},001 INFO mycron.scheduler: Executing job 'flaky-sync': curl -s",
                f"{stamp(15)},500 INFO mycron.scheduler: Job 'flaky-sync' FAILED (exit 1) in 1200ms",
                f"{stamp(3)},001 INFO mycron.scheduler: Executing job 'slow-batch': /bin/sleep 999",
            ]
        )
        + "\n"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Site:
    cfg: Config
    port: int
    db_path: Path
    db_hash: str

    def request(self, path: str, method: str = "GET", host: str | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, headers={"Host": host} if host else {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            conn.close()

    def get(self, path: str) -> str:
        status, _headers, body = self.request(path)
        assert status == 200, body
        return body


@pytest.fixture
def site(tmp_path):
    seed = tmp_path / "seed.db"
    _seed(seed)
    # 서비스는 원본이 아니라 복사한 임시 파일을 읽는다
    db_path = tmp_path / "copy" / "mycron.db"
    db_path.parent.mkdir()
    shutil.copy(seed, db_path)

    cfg = Config(db_path=db_path, pid_file=tmp_path / "mycron.pid", daemon_log=tmp_path / "daemon.log")
    cfg.pid_file.write_text(str(os.getpid()))
    _daemon_log(cfg.daemon_log)

    server = web.create_server(cfg, "127.0.0.1", 0, allow_hosts=("mycron.internal",))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Site(cfg=cfg, port=server.server_address[1], db_path=db_path, db_hash=_sha256(db_path))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ──────────────────────────── 페이지 응답 ────────────────────────────


def test_overview_lists_jobs_with_schedule_and_options(site):
    page = site.get("/")
    for name in ("daily-report", "flaky-sync", "disk-guard", "slow-batch", "old-job", "백업 a/b"):
        assert name in page
    assert "매일 08:30" in page  # cron 식을 사람 말로
    assert "30 8 * * *" in page  # 원문도 함께
    assert "15분마다" in page
    assert "평일 10:00, 14:00" in page
    assert "비활성" in page and "활성" in page
    assert "겹치면 건너뜀" in page and "겹쳐도 실행" in page
    assert "성공 알림 끔" in page and "성공 알림 켬" in page
    assert "제한 5분" in page and "제한 1시간 (기본)" in page
    assert 'lang="ko"' in page and 'name="viewport"' in page


def test_overview_shows_daemon_running_jobs_and_failures(site):
    page = site.get("/")
    assert "데몬 실행 중" in page
    assert f"PID {os.getpid()}" in page
    assert "지금 실행 중" in page
    assert "지금 실행 중인 작업이 없습니다" not in page
    assert "마지막 실행 실패" in page
    assert "시간 초과" in page
    assert "경고" in page


def test_status_api_has_next_run_last_result_and_running(site):
    data = json.loads(site.get("/api/status"))
    jobs = {job["name"]: job for job in data["jobs"]}

    assert data["daemon"]["running"] is True
    assert data["daemon"]["pid"] == os.getpid()
    assert [item["name"] for item in data["running"]] == ["slow-batch"]
    assert data["running"][0]["over_timeout"] is True  # 제한 5초인데 3분째

    assert jobs["daily-report"]["schedule"] == "매일 08:30"
    next_run = datetime.fromisoformat(jobs["daily-report"]["next_run_at"])
    assert (next_run.hour, next_run.minute) == (8, 30)
    assert next_run > datetime.now().astimezone()
    assert jobs["old-job"]["enabled"] is False
    assert jobs["old-job"]["next_run_at"] is None
    assert jobs["old-job"]["last_run"] is None

    assert jobs["daily-report"]["last_run"]["result"] == "ok"
    assert jobs["daily-report"]["last_run"]["duration_ms"] == 1200
    assert jobs["flaky-sync"]["last_run"] == {
        **jobs["flaky-sync"]["last_run"],
        "result": "fail",
        "exit_code": 1,
    }
    assert jobs["flaky-sync"]["recent_failures"] == 1
    assert jobs["flaky-sync"]["recent_runs"] == 3
    assert jobs["disk-guard"]["last_run"]["result"] == "alert"
    assert jobs["slow-batch"]["last_run"]["result"] == "timeout"
    assert jobs["slow-batch"]["running"] is True
    assert jobs["daily-report"]["running"] is False
    # 마지막 실행이 실패한 활성 작업이 맨 앞에 온다
    assert data["jobs"][0]["name"] in ("flaky-sync", "slow-batch")
    assert data["jobs"][-1]["name"] == "old-job"


def test_job_detail_shows_history_and_output_tail(site):
    page = site.get("/jobs/flaky-sync")
    assert "최근 실행 3건" in page
    assert "synced 12 items" in page
    assert "stdout" in page and "stderr" in page
    assert "fatal: upload failed" in page
    assert "exit 1" in page

    assert "최근 실행 1건" in site.get("/jobs/flaky-sync?n=1")
    assert "최근 실행 3건" in site.get("/jobs/flaky-sync?n=abc")


def test_job_detail_output_is_tail_only(site):
    page = site.get(SPECIAL_PATH)
    assert "백업 a/b" in page
    assert "line-300" in page and "line-221" in page
    assert "line-220" not in page and "line-001" not in page
    assert "마지막 80줄 / 전체 300줄" in page


def test_job_detail_marks_running(site):
    page = site.get("/jobs/slow-batch")
    assert "실행 중" in page
    assert "제한 시간을 넘겼습니다" in page
    assert "Command timed out after 5 seconds" in page


def test_unknown_paths_are_404(site):
    assert site.request("/jobs/no-such-job")[0] == 404
    assert site.request("/nope")[0] == 404
    assert site.request("/jobs/a/b")[0] == 404


def test_health_check(site):
    status, headers, body = site.request(web.HEALTH_PATH)
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body) == {"status": "ok", "db": "ok", "daemon": "running", "jobs": 6}


def test_head_has_no_body(site):
    status, headers, body = site.request("/", method="HEAD")
    assert status == 200
    assert int(headers["Content-Length"]) > 0
    assert body == ""


# ──────────────────────────── 가림 처리 ────────────────────────────


@pytest.mark.parametrize("path", ["/", "/api/status", "/jobs/flaky-sync", "/jobs/flaky-sync?n=200"])
def test_secrets_never_reach_the_page(site, path):
    page = site.get(path)
    for secret in SECRETS:
        assert secret not in page
    assert "[가림]" in page


def test_redaction_keeps_the_rest_of_the_command(site):
    page = site.get("/jobs/flaky-sync")
    assert "https://example.com/sync?mode=full" in page
    assert "API_KEY=[가림]" in page
    assert "/usr/local/bin/sync --verbose --retries 3" in page


def test_output_is_html_escaped(site):
    page = site.get("/jobs/flaky-sync")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_no_external_resources(site):
    import re

    for path in ("/", "/jobs/flaky-sync"):
        status, headers, page = site.request(path)
        assert status == 200
        assert not re.search(r"""(?:src|href)=["'](?:https?:)?//""", page)
        assert "<script src" not in page
        assert 'rel="stylesheet"' not in page
        assert "@import" not in page and "url(" not in page
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        assert headers["X-Content-Type-Options"] == "nosniff"


def test_tail_output_drops_a_cut_first_line():
    secret = "A1b2C3d4E5f6G7h8"
    # 끝쪽 창 밖에서 시작해 창 안으로 걸치는 한 줄: 잘린 조각째로 버려야 한다
    text = "x" * 10 + "KEY=" + secret * 4000 + "\nlast line\n"
    tail = web.tail_output(text)
    assert tail.text == "last line"
    assert secret not in tail.text


# ──────────────────────────── 읽기 전용 ────────────────────────────


def test_db_file_is_untouched_after_requests(site):
    for path in ("/", "/api/status", "/jobs/flaky-sync", SPECIAL_PATH, web.HEALTH_PATH, "/jobs/none"):
        site.request(path)
    assert _sha256(site.db_path) == site.db_hash
    leftovers = {p.name for p in site.db_path.parent.iterdir()} - {"mycron.db", "mycron.db-shm", "mycron.db-wal"}
    assert not leftovers


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/", "/jobs/flaky-sync", "/jobs/flaky-sync/run"])
def test_writes_are_rejected(site, method, path):
    status, headers, _body = site.request(path, method=method)
    assert status == 405
    assert headers["Allow"] == "GET, HEAD"
    assert _sha256(site.db_path) == site.db_hash


def test_connection_is_read_only(site):
    conn = database.connect_readonly(site.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 6
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE jobs SET enabled = 0")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM logs")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE x (y)")
    finally:
        conn.close()
    assert _sha256(site.db_path) == site.db_hash


def test_missing_db_is_reported_and_not_created(tmp_path):
    cfg = Config(
        db_path=tmp_path / "missing.db",
        pid_file=tmp_path / "mycron.pid",
        daemon_log=tmp_path / "daemon.log",
    )
    app = web.WebApp(cfg)
    page = app.handle("GET", "/", "127.0.0.1:8765")
    assert page.status == 503
    assert "DB" in page.body.decode()
    health = app.handle("GET", web.HEALTH_PATH, None)
    assert health.status == 503
    assert json.loads(health.body)["status"] == "error"
    assert app.handle("GET", "/api/status", None).status == 503
    assert not cfg.db_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_stale_pid_file_is_reported_but_kept(site, tmp_path):
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    site.cfg.pid_file.write_text(str(finished.pid))

    page = site.get("/")
    assert "데몬 꺼짐" in page
    assert "PID 파일만 남아 있고" in page
    # 데몬이 없으면 로그에 열린 실행이 있어도 "실행 중" 으로 보지 않는다
    assert "지금 실행 중인 작업이 없습니다" in page
    assert "데몬이 꺼져 있어 실행되지 않음" in page
    # mycron status 와 달리 PID 파일을 지우지 않는다
    assert site.cfg.pid_file.read_text() == str(finished.pid)
    assert json.loads(site.get(web.HEALTH_PATH))["daemon"] == "stopped"


# ──────────────────────────── Host 검사 ────────────────────────────


@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.0.0.1:8765", 200),
        ("localhost:8765", 200),
        ("[::1]:8765", 200),
        ("192.168.0.10", 200),
        ("mycron.internal", 200),
        ("MYCRON.internal:443", 200),
        ("evil.example.com", 403),
        ("127.0.0.1.evil.example.com:8765", 403),
    ],
)
def test_host_header_check(site, host, expected):
    assert site.request(web.HEALTH_PATH, host=host)[0] == expected


def test_any_host_can_be_allowed(site):
    app = web.WebApp(site.cfg, allow_hosts=("*",))
    assert app.handle("GET", web.HEALTH_PATH, "anything.example.com").status == 200


# ──────────────────────────── 로그 · 표기 ────────────────────────────


def test_parse_running_pairs_start_and_finish():
    lines = [
        "2026-11-23 08:00:00,000 INFO mycron.scheduler: Executing job 'gone': /bin/x",
        "2026-11-23 09:00:00,000 INFO mycron.daemon: mycron daemon started (PID 77)",
        "2026-11-23 09:10:00,000 INFO mycron.scheduler: Executing job 'a': /bin/a",
        "2026-11-23 09:10:00,100 INFO mycron.scheduler: Executing job 'b': /bin/b --x 'y': z",
        "2026-11-23 09:10:05,000 INFO apscheduler.executors.default: Job \"a (trigger: ...)\" executed successfully",
        "2026-11-23 09:10:05,000 INFO mycron.scheduler: Job 'a' SUCCESS in 5000ms",
        "2026-11-23 09:20:00,000 INFO mycron.scheduler: Executing job 'a': /bin/a",
        "2026-11-23 09:20:01,000 INFO mycron.scheduler: Job 'c' FAILED (exit -1) in 10ms",
        "잘린 줄 mycron.scheduler: Executing job 'broken': x",
    ]
    running = web.parse_running(lines)
    assert [(name, started.strftime("%H:%M:%S")) for name, started in running] == [
        ("b", "09:10:00"),
        ("a", "09:20:00"),
    ]


def test_classify():
    assert web.classify(0, None) == "ok"
    assert web.classify(10, None) == "alert"
    assert web.classify(-1, "Command timed out after 5 seconds") == "timeout"
    assert web.classify(-1, "No such file") == "fail"
    assert web.classify(2, None) == "fail"


def test_time_and_duration_text():
    now = datetime(2026, 11, 24, 0, 12, tzinfo=timezone(timedelta(hours=9)))
    assert webui.fmt_abs(now + timedelta(minutes=3), now) == "오늘 00:15"
    assert webui.fmt_abs(now - timedelta(hours=1), now) == "어제 23:12"
    assert webui.fmt_abs(now + timedelta(days=1, hours=8), now) == "내일 08:12"
    assert webui.fmt_abs(datetime(2026, 11, 28, 10, 0, tzinfo=now.tzinfo), now) == "11/28(토) 10:00"
    assert webui.fmt_rel(now + timedelta(minutes=3), now) == "3분 뒤"
    assert webui.fmt_rel(now - timedelta(hours=2, minutes=5), now) == "2시간 5분 전"
    assert webui.fmt_rel(now - timedelta(days=8, hours=3), now) == "8일 전"
    assert webui.fmt_duration(420) == "0.4초"
    assert webui.fmt_duration(14_987) == "15초"
    assert webui.fmt_duration(794_000) == "13분 14초"
    assert webui.fmt_duration(4_320_000) == "1시간 12분"
    assert webui.fmt_timeout(3600) == "1시간"
    assert webui.fmt_timeout(300) == "5분"
    assert webui.fmt_timeout(90) == "1분 30초"
    assert webui.fmt_timeout(12.5) == "12.5초"
    assert webui.exit_text(-15) == "exit -15 (신호 15)"


# ──────────────────────────── CLI · LaunchAgent ────────────────────────────


def test_web_command_refuses_a_busy_port(site):
    assert web.port_in_use("127.0.0.1", site.port)
    with patch("mycron.cli.load_config", return_value=site.cfg):
        result = CliRunner().invoke(main, ["web", "--port", str(site.port)])
    assert result.exit_code == 1
    assert "이미" in result.output and "--port" in result.output


def test_web_defaults():
    assert (web.DEFAULT_HOST, web.DEFAULT_PORT, web.HEALTH_PATH) == ("127.0.0.1", 8765, "/healthz")
    params = {param.name: param.default for param in main.commands["web"].params}
    assert params["host"] == web.DEFAULT_HOST
    assert params["port"] == web.DEFAULT_PORT


def test_web_launch_agent_plist(tmp_path):
    plist_path = tmp_path / "LaunchAgents" / "web.plist"
    with (
        patch.object(launchd, "WEB_PLIST_PATH", plist_path),
        patch.object(launchd, "_find_mycron_bin", return_value="/opt/bin/mycron"),
        patch.object(launchd.subprocess, "run") as run,
    ):
        launchd.install_web("127.0.0.1", 8765, ("mycron.internal",))
        run.assert_called_once_with(["launchctl", "load", str(plist_path)], check=True)
        plist = plistlib.loads(plist_path.read_bytes())
        assert plist["Label"] == "com.dysim.mycron-web" != launchd.LABEL
        assert plist["ProgramArguments"] == [
            "/opt/bin/mycron", "web", "--host", "127.0.0.1", "--port", "8765",
            "--allow-host", "mycron.internal",
        ]
        assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True

        launchd.uninstall_web()
        run.assert_called_with(["launchctl", "unload", str(plist_path)], check=True)
        assert not plist_path.exists()


# ──────────────────────────── 실제 DB 복사본 ────────────────────────────


@pytest.mark.skipif(not (MYCRON_DIR / "mycron.db").exists(), reason="이 기기에 mycron DB 가 없음")
def test_pages_render_from_a_copy_of_the_real_db(tmp_path):
    source = database.connect_readonly(MYCRON_DIR / "mycron.db")
    db_path = tmp_path / "mycron.db"
    target = sqlite3.connect(db_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    before = _sha256(db_path)

    cfg = Config(db_path=db_path, pid_file=tmp_path / "mycron.pid", daemon_log=tmp_path / "daemon.log")
    app = web.WebApp(cfg)
    overview = app.handle("GET", "/", None)
    assert overview.status == 200

    names = [job["name"] for job in json.loads(app.handle("GET", "/api/status", None).body)["jobs"]]
    from urllib.parse import quote

    for name in names:
        detail = app.handle("GET", f"/jobs/{quote(name, safe='')}?n=50", None)
        assert detail.status == 200, name
        assert name in detail.body.decode()
    assert _sha256(db_path) == before
