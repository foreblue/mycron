import sys
from datetime import datetime, timezone

import click

from .config import load_config
from . import db as database
from . import daemon
from . import launchd
from .executor import run_command
from .notifier import send as notify


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
def add(name, cron_expr, command, skip_if_running, no_success_notify):
    """새 작업을 등록합니다."""
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
    )
    tags = []
    if job.skip_if_running:
        tags.append("skip_if_running")
    if not job.notify_on_success:
        tags.append("no_success_notify")
    tag_str = f" [{','.join(tags)}]" if tags else ""
    click.echo(f"작업 등록됨: {job.name} ({job.cron_expr}) → {job.command}{tag_str}")
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

    header = f"{'이름':<20} {'Cron':<15} {'상태':<8} {'옵션':<18} {'커맨드'}"
    click.echo(header)
    click.echo("-" * 90)
    for job in jobs:
        status = "활성" if job.enabled else "비활성"
        opt_parts = []
        if job.skip_if_running:
            opt_parts.append("skip")
        if not job.notify_on_success:
            opt_parts.append("no-notify-ok")
        opts = ",".join(opt_parts)
        cmd = job.command if len(job.command) <= 30 else job.command[:27] + "..."
        click.echo(f"{job.name:<20} {job.cron_expr:<15} {status:<8} {opts:<18} {cmd}")


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

    click.echo(f"실행 중: {job.command}")
    result = run_command(job.command)

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
    status = "성공" if result.success else f"실패 (exit {result.exit_code})"
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
        status = "OK" if entry.exit_code == 0 else f"FAIL({entry.exit_code})"
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
