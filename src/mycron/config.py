import tomllib
from dataclasses import dataclass, field
from pathlib import Path


MYCRON_DIR = Path.home() / ".mycron"


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass
class Config:
    db_path: Path = field(default_factory=lambda: MYCRON_DIR / "mycron.db")
    pid_file: Path = field(default_factory=lambda: MYCRON_DIR / "mycron.pid")
    daemon_log: Path = field(default_factory=lambda: MYCRON_DIR / "daemon.log")
    log_retention_days: int = 30
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


def load_config() -> Config:
    MYCRON_DIR.mkdir(parents=True, exist_ok=True)
    config_path = MYCRON_DIR / "config.toml"
    cfg = Config()

    if not config_path.exists():
        return cfg

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    if "general" in data:
        g = data["general"]
        cfg.log_retention_days = g.get("log_retention_days", cfg.log_retention_days)

    if "telegram" in data:
        t = data["telegram"]
        cfg.telegram = TelegramConfig(
            bot_token=t.get("bot_token", ""),
            chat_id=t.get("chat_id", ""),
        )

    return cfg
