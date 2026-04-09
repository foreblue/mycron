import logging
import sqlite3

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db as database
from .config import Config
from .executor import run_command
from .notifier import send

logger = logging.getLogger(__name__)


def create_scheduler(cfg: Config, conn: sqlite3.Connection) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    _register_jobs(scheduler, cfg, conn)
    _register_maintenance(scheduler, cfg, conn)
    return scheduler


def reload_jobs(scheduler: BackgroundScheduler, cfg: Config, conn: sqlite3.Connection) -> None:
    # Remove all user jobs (keep maintenance job)
    for job in scheduler.get_jobs():
        if job.id != "_maintenance":
            scheduler.remove_job(job.id)
    _register_jobs(scheduler, cfg, conn)
    logger.info("Jobs reloaded from DB")


def _register_jobs(scheduler: BackgroundScheduler, cfg: Config, conn: sqlite3.Connection) -> None:
    jobs = database.list_jobs(conn, include_disabled=False)
    for job in jobs:
        trigger = CronTrigger.from_crontab(job.cron_expr)
        scheduler.add_job(
            _execute_job,
            trigger=trigger,
            id=f"job_{job.id}",
            name=job.name,
            args=[cfg, conn, job.id, job.name, job.command],
            replace_existing=True,
        )
        logger.info("Registered job '%s' with cron '%s'", job.name, job.cron_expr)


def _register_maintenance(scheduler: BackgroundScheduler, cfg: Config, conn: sqlite3.Connection) -> None:
    scheduler.add_job(
        _prune_logs,
        CronTrigger(hour=4, minute=0),
        id="_maintenance",
        args=[cfg, conn],
        replace_existing=True,
    )


def _execute_job(cfg: Config, conn: sqlite3.Connection, job_id: int, job_name: str, command: str) -> None:
    logger.info("Executing job '%s': %s", job_name, command)
    result = run_command(command)

    log_id = database.insert_log(
        conn,
        job_id=job_id,
        started_at=result.started_at,
        finished_at=result.finished_at,
        duration_ms=result.duration_ms,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )

    status = "SUCCESS" if result.success else f"FAILED (exit {result.exit_code})"
    logger.info("Job '%s' %s in %dms", job_name, status, result.duration_ms)

    notified = send(cfg.telegram, job_name, result)
    if notified:
        database.mark_log_notified(conn, log_id)


def _prune_logs(cfg: Config, conn: sqlite3.Connection) -> None:
    deleted = database.prune_logs(conn, cfg.log_retention_days)
    logger.info("Pruned %d old log entries (retention: %d days)", deleted, cfg.log_retention_days)
