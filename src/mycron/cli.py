import sys
from datetime import datetime, timezone

import click

from .config import load_config
from . import db as database
from . import daemon
from . import launchd
from .executor import ALERT_EXIT_CODE, DEFAULT_TIMEOUT_SECONDS, run_command
from .notifier import send as notify


TIMEOUT_TYPE = click.FloatRange(min=0, min_open=True)
# 상태판 카드에 한 줄로 들어가는 글이라 길이를 막아 둔다
MAX_DESCRIPTION_LENGTH = 200


def _clean_description(text: str) -> str:
    description = database.clean_description(text)
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise click.BadParameter(
            f"설명은 {MAX_DESCRIPTION_LENGTH}자까지입니다 (지금 {len(description)}자).",
            param_hint="설명",
        )
    return description


def _fmt_local(ts: str) -> str:
    """UTC로 저장된 ISO 타임스탬프를 로컬 타임존으로 변환해 표시합니다."""
    try:
        return (
            datetime.fromisoformat(ts)
            .replace(tzinfo=timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%dT%H:%M:%S")
        )
    except ValueError:
        return ts


def _fmt_timeout(timeout_seconds: float) -> str:
    if float(timeout_seconds).is_integer():
        return str(int(timeout_seconds))
    return str(timeout_seconds)


@click.group()
def main():
    """mycron - cron 스타일 작업 스케줄러"""


# ──────────────────────────── job management ────────────────────────────

@main.command()
@click.option("--name", required=True, help="작업 이름")
@click.option("--cron", "cron_expr", required=True, help='Cron 표현식 (예: "0 * * * *")')
@click.option("--command", required=True, help="실행할 쉘 커맨드")
@click.option("--skip-if-running", is_flag=True, default=False, help="이전 실행이 진행 중이면 새 실행을 건너뜁니다")
@click.option("--no-success-notify", is_flag=True, default=False, help="성공 시 텔레그램 알림을 보내지 않습니다 (실패 시만 알림)")
@click.option(
    "--timeout",
    "timeout_seconds",
    type=TIMEOUT_TYPE,
    default=DEFAULT_TIMEOUT_SECONDS,
    show_default=True,
    help="작업 실행 제한 시간(초)",
)
@click.option("--description", default="", help="어떤 작업인지 한 줄 설명 (상태판에 표시)")
def add(name, cron_expr, command, skip_if_running, no_success_notify, timeout_seconds, description):
    """새 작업을 등록합니다."""
    description = _clean_description(description)
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    if database.get_job(conn, name):
        click.echo(f"오류: '{name}' 이름의 작업이 이미 존재합니다.", err=True)
        sys.exit(1)

    job = database.add_job(
        conn,
        name,
        cron_expr,
        command,
        skip_if_running=skip_if_running,
        notify_on_success=not no_success_notify,
        timeout_seconds=timeout_seconds,
        description=description,
    )
    tags = []
    if job.skip_if_running:
        tags.append("skip_if_running")
    if not job.notify_on_success:
        tags.append("no_success_notify")
    if job.timeout_seconds != DEFAULT_TIMEOUT_SECONDS:
        tags.append(f"timeout={_fmt_timeout(job.timeout_seconds)}s")
    tag_str = f" [{','.join(tags)}]" if tags else ""
    click.echo(f"작업 등록됨: {job.name} ({job.cron_expr}) → {job.command}{tag_str}")
    if job.description:
        click.echo(f"설명: {job.description}")
    daemon.signal_reload(cfg)


@main.command()
@click.argument("name")
def remove(name):
    """등록된 작업을 삭제합니다."""
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    if not database.remove_job(conn, name):
        click.echo(f"오류: '{name}' 작업을 찾을 수 없습니다.", err=True)
        sys.exit(1)

    click.echo(f"작업 삭제됨: {name}")
    daemon.signal_reload(cfg)


@main.command("list")
@click.option("--all", "include_all", is_flag=True, help="비활성 작업 포함")
def list_jobs(include_all):
    """등록된 작업 목록을 표시합니다."""
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    jobs = database.list_jobs(conn, include_disabled=include_all)
    if not jobs:
        click.echo("등록된 작업이 없습니다.")
        return

    header = f"{'이름':<20} {'Cron':<15} {'상태':<8} {'옵션':<32} {'커맨드'}"
    click.echo(header)
    click.echo("-" * 110)
    for job in jobs:
        status = "활성" if job.enabled else "비활성"
        opt_parts = []
        if job.skip_if_running:
            opt_parts.append("skip")
        if not job.notify_on_success:
            opt_parts.append("no-notify-ok")
        if job.timeout_seconds != DEFAULT_TIMEOUT_SECONDS:
            opt_parts.append(f"timeout={_fmt_timeout(job.timeout_seconds)}s")
        opts = ",".join(opt_parts)
        cmd = job.command if len(job.command) <= 30 else job.command[:27] + "..."
        click.echo(f"{job.name:<20} {job.cron_expr:<15} {status:<8} {opts:<32} {cmd}")


@main.command()
@click.argument("name")
def enable(name):
    """비활성 작업을 활성화합니다."""
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    if not database.set_job_enabled(conn, name, True):
        click.echo(f"오류: '{name}' 작업을 찾을 수 없습니다.", err=True)
        sys.exit(1)

    click.echo(f"작업 활성화됨: {name}")
    daemon.signal_reload(cfg)


@main.command()
@click.argument("name")
def disable(name):
    """작업을 비활성화합니다 (삭제하지 않음)."""
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    if not database.set_job_enabled(conn, name, False):
        click.echo(f"오류: '{name}' 작업을 찾을 수 없습니다.", err=True)
        sys.exit(1)

    click.echo(f"작업 비활성화됨: {name}")
    daemon.signal_reload(cfg)


@main.command("set-notify")
@click.argument("name")
@click.option("--on-success/--no-success", default=True, help="성공 시 알림 여부 (기본: --on-success)")
def set_notify(name, on_success):
    """작업의 성공 알림 여부를 변경합니다."""
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    if not database.set_job_notify_on_success(conn, name, on_success):
        click.echo(f"오류: '{name}' 작업을 찾을 수 없습니다.", err=True)
        sys.exit(1)

    state = "ON" if on_success else "OFF"
    click.echo(f"성공 알림 {state}: {name}")
    daemon.signal_reload(cfg)


@main.command("set-timeout")
@click.argument("name")
@click.option(
    "--timeout",
    "timeout_seconds",
    type=TIMEOUT_TYPE,
    required=True,
    help="작업 실행 제한 시간(초)",
)
def set_timeout(name, timeout_seconds):
    """작업의 실행 제한 시간을 변경합니다."""
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    if not database.set_job_timeout(conn, name, timeout_seconds):
        click.echo(f"오류: '{name}' 작업을 찾을 수 없습니다.", err=True)
        sys.exit(1)

    click.echo(f"Timeout 설정됨: {name} ({_fmt_timeout(timeout_seconds)}s)")
    daemon.signal_reload(cfg)


@main.command()
@click.argument("name")
@click.argument("description", required=False, default=None)
def describe(name, description):
    """작업에 한 줄 설명을 붙입니다. 설명을 생략하면 지금 설명을 보여 주고, "" 를 주면 지웁니다."""
    if description is not None:
        description = _clean_description(description)
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    job = database.get_job(conn, name)
    if not job:
        click.echo(f"오류: '{name}' 작업을 찾을 수 없습니다.", err=True)
        sys.exit(1)

    if description is None:
        click.echo(job.description)
        return

    # 설명은 스케줄과 무관하므로 데몬을 리로드하지 않는다
    database.set_job_description(conn, name, description)
    if description:
        click.echo(f"설명 설정됨: {name} — {description}")
    else:
        click.echo(f"설명 지움: {name}")


# ──────────────────────────── execution ────────────────────────────

@main.command()
@click.argument("name")
def run(name):
    """작업을 즉시 실행합니다 (테스트용)."""
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    job = database.get_job(conn, name)
    if not job:
        click.echo(f"오류: '{name}' 작업을 찾을 수 없습니다.", err=True)
        sys.exit(1)

    click.echo(f"실행 중: {job.command} (timeout: {_fmt_timeout(job.timeout_seconds)}s)")
    result = run_command(job.command, timeout=job.timeout_seconds)

    log_id = database.insert_log(
        conn,
        job_id=job.id,
        started_at=result.started_at,
        finished_at=result.finished_at,
        duration_ms=result.duration_ms,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )

    notified = notify(cfg.telegram, job.name, result, notify_on_success=job.notify_on_success)
    if notified:
        database.mark_log_notified(conn, log_id)

    duration_s = result.duration_ms / 1000
    if result.success:
        status = "성공"
    elif result.is_alert:
        status = f"경고 (exit {ALERT_EXIT_CODE})"
    else:
        status = f"실패 (exit {result.exit_code})"
    click.echo(f"결과: {status} ({duration_s:.1f}s)")

    if result.stdout:
        click.echo("--- stdout ---")
        click.echo(result.stdout)
    if result.stderr:
        click.echo("--- stderr ---")
        click.echo(result.stderr, err=True)

    if not result.success:
        sys.exit(result.exit_code)


# ──────────────────────────── logs ────────────────────────────

@main.command()
@click.argument("name", required=False, default=None)
@click.option("--limit", default=20, show_default=True, help="표시할 로그 수")
def logs(name, limit):
    """실행 로그를 조회합니다. NAME 생략 시 전체 작업 로그를 표시합니다."""
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    if name:
        job = database.get_job(conn, name)
        if not job:
            click.echo(f"오류: '{name}' 작업을 찾을 수 없습니다.", err=True)
            sys.exit(1)
        job_id = job.id
        header = f"작업: {name}  (최근 {limit}건)"
    else:
        job_id = None
        header = f"전체 실행 로그  (최근 {limit}건)"

    entries = database.get_logs(conn, job_id, limit)
    if not entries:
        click.echo("실행 로그가 없습니다.")
        return

    click.echo(header)
    click.echo("-" * 65)
    for entry in entries:
        if entry.exit_code == 0:
            status = "OK"
        elif entry.exit_code == ALERT_EXIT_CODE:
            status = "ALERT"
        else:
            status = f"FAIL({entry.exit_code})"
        duration_s = entry.duration_ms / 1000
        notified = " [알림전송]" if entry.notified else ""
        job_col = f"[{entry.job_name}]" if not name else ""
        click.echo(f"{_fmt_local(entry.started_at)}  {status:<10} {duration_s:>7.1f}s  {job_col}{notified}")
        if entry.stderr and entry.exit_code != 0:
            snippet = entry.stderr[:200].replace("\n", " ")
            click.echo(f"  stderr: {snippet}")


# ──────────────────────────── daemon ────────────────────────────

@main.command()
@click.option("--foreground", "-f", is_flag=True, help="포그라운드로 실행 (디버그용)")
def start(foreground):
    """스케줄러 데몬을 시작합니다."""
    cfg = load_config()
    conn = database.connect(cfg.db_path)
    database.init_db(conn)
    conn.close()
    daemon.start(cfg, foreground=foreground)


@main.command()
def stop():
    """스케줄러 데몬을 중지합니다."""
    cfg = load_config()
    daemon.stop(cfg)


@main.command()
def status():
    """스케줄러 데몬의 상태를 확인합니다."""
    cfg = load_config()
    daemon.status(cfg)


# ──────────────────────────── launchd ────────────────────────────

@main.command()
def install():
    """macOS LaunchAgent를 등록합니다 (재시작 후 자동 실행)."""
    launchd.install()


@main.command()
def uninstall():
    """macOS LaunchAgent를 해제합니다."""
    launchd.uninstall()


# ──────────────────────────── web (읽기 전용 상태판) ────────────────────────────

_WEB_OPTIONS = [
    click.option("--host", default="127.0.0.1", show_default=True, help="바인드 주소"),
    click.option("--port", default=8765, show_default=True, type=click.IntRange(1, 65535), help="포트"),
    click.option(
        "--allow-host",
        "allow_hosts",
        multiple=True,
        help="접속을 허용할 Host 이름 (게이트웨이 이름 등, 여러 번 지정 가능). IP · localhost 는 항상 허용",
    ),
]


def _web_options(func):
    for option in reversed(_WEB_OPTIONS):
        func = option(func)
    return func


@main.command()
@_web_options
@click.option("--verbose", is_flag=True, help="요청 로그를 남깁니다")
def web(host, port, allow_hosts, verbose):
    """작업과 실행 상태를 브라우저로 보는 읽기 전용 웹서비스를 띄웁니다."""
    from . import web as webserver

    cfg = load_config()
    try:
        webserver.serve(cfg, host=host, port=port, allow_hosts=allow_hosts, verbose=verbose)
    except webserver.PortInUse as e:
        click.echo(f"오류: {e} --port 로 다른 포트를 지정하세요.", err=True)
        sys.exit(1)


@main.command("web-install")
@_web_options
def web_install(host, port, allow_hosts):
    """웹서비스를 macOS LaunchAgent로 등록합니다 (재시작 후 자동 실행)."""
    launchd.install_web(host, port, allow_hosts)


@main.command("web-uninstall")
def web_uninstall():
    """웹서비스 LaunchAgent를 해제합니다."""
    launchd.uninstall_web()
