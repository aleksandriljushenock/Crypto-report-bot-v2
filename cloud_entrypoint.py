from __future__ import annotations

import atexit
import os
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import IO


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
LOG_DIR = Path(os.getenv("LOG_DIR", str(BASE_DIR / "logs"))).expanduser().resolve()
LOCK_PATH = DATA_DIR / "telegram_bot.lock"

_lock_file: IO[str] | None = None


def _prepare_runtime() -> None:
    os.chdir(BASE_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _acquire_single_instance_lock() -> None:
    global _lock_file
    _lock_file = LOCK_PATH.open("a+", encoding="utf-8")

    try:
        if os.name == "nt":
            import msvcrt

            _lock_file.seek(0)
            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        raise RuntimeError(
            "Другой экземпляр Telegram-бота уже использует этот DATA_DIR. "
            "Оставь одну Railway replica и один активный deployment."
        ) from exc

    _lock_file.seek(0)
    _lock_file.truncate()
    _lock_file.write(str(os.getpid()))
    _lock_file.flush()


def _release_lock() -> None:
    global _lock_file
    if _lock_file is None:
        return

    try:
        if os.name == "nt":
            import msvcrt

            _lock_file.seek(0)
            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_file.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        _lock_file.close()
        _lock_file = None


def _handle_termination(signum: int, frame: FrameType | None) -> None:
    del frame
    print(f"Получен сигнал остановки {signum}. Завершаю Telegram-бот...", flush=True)
    raise KeyboardInterrupt


def _validate_environment() -> None:
    required = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(
            "Не заданы обязательные переменные окружения: " + ", ".join(missing)
        )


def main() -> int:
    _prepare_runtime()
    _validate_environment()
    _acquire_single_instance_lock()
    atexit.register(_release_lock)

    signal.signal(signal.SIGTERM, _handle_termination)
    signal.signal(signal.SIGINT, _handle_termination)

    from telegram_command_bot import listen

    listen()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as exc:
        print(f"Ошибка запуска облачного бота: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
