from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DB_PATH = Path("habits.db")
DEFAULT_TIMEZONE = "Europe/Moscow"


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class BotConfig:
    telegram_bot_token: str
    db_path: Path = DEFAULT_DB_PATH
    timezone: str = DEFAULT_TIMEZONE

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load_dotenv_file(path: Path | str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(env: Mapping[str, str] | None = None) -> BotConfig:
    source = os.environ if env is None else env
    token = source.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("Missing required environment variable: TELEGRAM_BOT_TOKEN")

    timezone = source.get("HABITS_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unknown timezone in HABITS_TIMEZONE: {timezone}") from exc

    db_path = Path(source.get("HABITS_DB_PATH", str(DEFAULT_DB_PATH)).strip() or DEFAULT_DB_PATH)
    return BotConfig(telegram_bot_token=token, db_path=db_path, timezone=timezone)
