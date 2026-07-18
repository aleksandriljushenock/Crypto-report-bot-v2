from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: Optional[int] = None) -> int:
    try:
        value = int(env_str(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value) if minimum is not None else value


def env_float(name: str, default: float, minimum: Optional[float] = None) -> float:
    try:
        value = float(env_str(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value) if minimum is not None else value


@dataclass(frozen=True)
class AppSettings:
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    log_dir: Path = BASE_DIR / "logs"
    telegram_bot_token: str = env_str("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = env_str("TELEGRAM_CHAT_ID")
    http_connect_timeout: int = env_int("HTTP_CONNECT_TIMEOUT", 8, 1)
    http_read_timeout: int = env_int("HTTP_READ_TIMEOUT", 35, 1)
    http_retries: int = env_int("HTTP_RETRIES", 3, 0)
    http_backoff: float = env_float("HTTP_BACKOFF_FACTOR", 0.6, 0.0)
    cache_ttl_seconds: int = env_int("HTTP_CACHE_TTL_SECONDS", 60, 0)
    log_level: str = env_str("LOG_LEVEL", "INFO").upper()
    log_max_bytes: int = env_int("LOG_MAX_BYTES", 5_000_000, 100_000)
    log_backup_count: int = env_int("LOG_BACKUP_COUNT", 5, 1)

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def validate(self, require_telegram: bool = False) -> list[str]:
        errors: list[str] = []
        if require_telegram and not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN отсутствует в .env")
        if self.http_connect_timeout <= 0 or self.http_read_timeout <= 0:
            errors.append("HTTP timeout должен быть больше нуля")
        return errors


settings = AppSettings()
settings.ensure_directories()
