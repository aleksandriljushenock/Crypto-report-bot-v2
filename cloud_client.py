from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from cloud_config import (
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
    validate_cloud_config,
)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """
    Создаёт клиент Supabase.

    Благодаря lru_cache клиент создаётся только один раз,
    а затем повторно используется во всём приложении.
    """

    validate_cloud_config()

    return create_client(
        SUPABASE_URL,
        SUPABASE_SERVICE_KEY,
    )


# Эта строка оставлена для совместимости с кодом:
# from cloud_client import supabase
supabase: Client = get_supabase_client()