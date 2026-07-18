from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


# Полный путь к папке, в которой находится cloud_config.py.
BASE_DIR = Path(__file__).resolve().parent

# Загружаем переменные из файла .env в корне проекта.
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)


# Получаем настройки Supabase из .env.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()

SUPABASE_SERVICE_KEY = (
    os.getenv("SUPABASE_SERVICE_KEY")
    or os.getenv("SUPABASE_KEY")
    or ""
).strip()

SUPABASE_MODEL_BUCKET = os.getenv(
    "SUPABASE_MODEL_BUCKET",
    "models",
).strip()


def validate_cloud_config() -> None:
    """
    Проверяет, что обязательные настройки Supabase
    присутствуют и имеют ожидаемый формат.

    Функция ничего не возвращает.
    При ошибке она выбрасывает RuntimeError
    с понятным сообщением.
    """

    if not SUPABASE_URL:
        raise RuntimeError(
            "Переменная SUPABASE_URL отсутствует в окружении или .env"
        )

    if not SUPABASE_URL.startswith("https://"):
        raise RuntimeError(
            "SUPABASE_URL должен начинаться с https://"
        )

    if ".supabase.co" not in SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL не похож на адрес проекта Supabase"
        )

    if not SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "Переменная SUPABASE_SERVICE_KEY (или SUPABASE_KEY) отсутствует в окружении или .env"
        )

    if not SUPABASE_MODEL_BUCKET:
        raise RuntimeError(
            "Переменная SUPABASE_MODEL_BUCKET пустая"
        )