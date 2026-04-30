import json
import logging
import urllib.error
import urllib.request
from urllib.parse import urlencode

from .config import TelegramConfig
from .executor import ExecutionResult

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 10


def send(
    cfg: TelegramConfig,
    job_name: str,
    result: ExecutionResult,
    notify_on_success: bool = True,
) -> bool:
    if not cfg.enabled:
        return False
    if result.success and not notify_on_success:
        return False

    message = _build_message(job_name, result)
    url = TELEGRAM_API.format(token=cfg.bot_token)

    payload = json.dumps({"chat_id": cfg.chat_id, "text": message}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT):
            return True
    except urllib.error.HTTPError as e:
        logger.warning("Telegram HTTP error %s: %s", e.code, e.reason)
    except urllib.error.URLError as e:
        logger.warning("Telegram URL error: %s", e.reason)
    except Exception as e:
        logger.warning("Telegram unexpected error: %s", e)

    return False


def send_text(cfg: TelegramConfig, text: str) -> bool:
    """임의의 텍스트를 Telegram으로 전송합니다."""
    if not cfg.enabled:
        return False

    url = TELEGRAM_API.format(token=cfg.bot_token)
    payload = json.dumps({"chat_id": cfg.chat_id, "text": text}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT):
            return True
    except urllib.error.HTTPError as e:
        logger.warning("Telegram HTTP error %s: %s", e.code, e.reason)
    except urllib.error.URLError as e:
        logger.warning("Telegram URL error: %s", e.reason)
    except Exception as e:
        logger.warning("Telegram unexpected error: %s", e)

    return False


def _build_message(job_name: str, result: ExecutionResult) -> str:
    duration_s = result.duration_ms / 1000
    if result.success:
        lines = [
            f'[mycron] Job "{job_name}" completed',
            f"  Status: SUCCESS (exit 0)",
            f"  Duration: {duration_s:.1f}s",
        ]
    else:
        lines = [
            f'[mycron] Job "{job_name}" FAILED',
            f"  Status: FAILED (exit {result.exit_code})",
            f"  Duration: {duration_s:.1f}s",
        ]
        if result.stderr:
            snippet = result.stderr[:500]
            lines.append(f"  Stderr: {snippet}")

    return "\n".join(lines)
