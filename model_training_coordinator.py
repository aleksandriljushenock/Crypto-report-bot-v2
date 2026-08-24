"""Process-wide coordinator for every model-training path.

The VPS runs all learners in one process. A single lock prevents V14, cloud overlay
and the Adaptive Paper model from training concurrently and competing for the same
CPU/data snapshots. Supabase promotion itself is additionally serialized by the
V43 PostgreSQL advisory lock.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_LOCK = threading.Lock()


@contextmanager
def training_slot():
    acquired = _LOCK.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _LOCK.release()


def training_running() -> bool:
    return _LOCK.locked()
