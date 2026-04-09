import logging
import logging.handlers
import os
import signal
import sys
import time

from .config import Config, load_config
from . import db as database
from .scheduler import create_scheduler, reload_jobs

logger = logging.getLogger(__name__)


def start(cfg: Config, foreground: bool = False) -> None:
    if _is_running(cfg):
        print(f"mycron is already running (PID {_read_pid(cfg)})")
        sys.exit(1)

    if foreground:
        _run_scheduler(cfg)
    else:
        _daemonize(cfg)


def stop(cfg: Config) -> None:
    pid = _read_pid(cfg)
    if pid is None or not _is_running(cfg):
        print("mycron is not running")
        sys.exit(1)

    os.kill(pid, signal.SIGTERM)
    for _ in range(100):  # wait up to 10s
        time.sleep(0.1)
        if not _is_running(cfg):
            print(f"mycron stopped (PID {pid})")
            return

    # Force kill if still alive
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    print(f"mycron force-killed (PID {pid})")


def status(cfg: Config) -> None:
    pid = _read_pid(cfg)
    if pid is None:
        print("mycron is not running")
        return
    if _is_running(cfg):
        print(f"mycron is running (PID {pid})")
    else:
        print("mycron is not running (stale PID file)")
        cfg.pid_file.unlink(missing_ok=True)


def signal_reload(cfg: Config) -> None:
    pid = _read_pid(cfg)
    if pid and _is_running(cfg):
        os.kill(pid, signal.SIGHUP)


def _daemonize(cfg: Config) -> None:
    # First fork
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    os.setsid()

    # Second fork
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # Redirect stdio to /dev/null
    devnull = open(os.devnull, "r+")
    os.dup2(devnull.fileno(), sys.stdin.fileno())
    os.dup2(devnull.fileno(), sys.stdout.fileno())
    os.dup2(devnull.fileno(), sys.stderr.fileno())

    _run_scheduler(cfg)


def _run_scheduler(cfg: Config) -> None:
    _setup_logging(cfg)

    cfg.pid_file.write_text(str(os.getpid()))
    logger.info("mycron daemon started (PID %d)", os.getpid())

    conn = database.connect(cfg.db_path)
    database.init_db(conn)

    scheduler = create_scheduler(cfg, conn)
    scheduler.start()

    def handle_sigterm(signum, frame):
        logger.info("Received SIGTERM, shutting down...")
        scheduler.shutdown(wait=True)
        cfg.pid_file.unlink(missing_ok=True)
        logger.info("mycron daemon stopped")
        sys.exit(0)

    def handle_sighup(signum, frame):
        logger.info("Received SIGHUP, reloading jobs...")
        reload_jobs(scheduler, cfg, conn)

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGHUP, handle_sighup)

    while True:
        signal.pause()


def _setup_logging(cfg: Config) -> None:
    handler = logging.handlers.RotatingFileHandler(
        cfg.daemon_log,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


def _read_pid(cfg: Config) -> int | None:
    if not cfg.pid_file.exists():
        return None
    try:
        return int(cfg.pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def _is_running(cfg: Config) -> bool:
    pid = _read_pid(cfg)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
