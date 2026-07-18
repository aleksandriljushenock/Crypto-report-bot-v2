from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None


def to_json_safe(value: Any) -> Any:
    """
    Преобразует Python-значение в формат,
    который можно безопасно отправить в JSON/Supabase.
    """

    if value is None:
        return None

    if isinstance(value, dict):
        return {
            str(key): to_json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(item) for item in value]

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if np is not None:
        if isinstance(value, np.integer):
            return int(value)

        if isinstance(value, np.floating):
            converted = float(value)

            if np.isnan(converted) or np.isinf(converted):
                return None

            return converted

        if isinstance(value, np.ndarray):
            return [
                to_json_safe(item)
                for item in value.tolist()
            ]

    if isinstance(value, float):
        if value != value:
            return None

        if value == float("inf") or value == float("-inf"):
            return None

        return value

    if isinstance(value, (str, int, bool)):
        return value

    return str(value)