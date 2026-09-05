from __future__ import annotations

import itertools
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

_COND = threading.Condition()
_SEQ = itertools.count()
_ACTIVE: dict | None = None
_WAITING: list[dict] = []

_DEFAULT_PRIORITIES = {
    'execution-v57-model-trainer': 10,
    'self-learning-engine': 20,
    'learning-checkpoint': 30,
    'ai-optimizer-adaptive-models': 40,
    'profit-profile-rebuild': 50,
    'strategy-lab-auto': 60,
}


def _priority(name: str) -> int:
    env_name = 'HEAVY_TASK_PRIORITY_' + name.upper().replace('-', '_')
    try:
        return int(os.getenv(env_name, str(_DEFAULT_PRIORITIES.get(name, 100))))
    except Exception:
        return _DEFAULT_PRIORITIES.get(name, 100)


def snapshot() -> dict:
    with _COND:
        active = dict(_ACTIVE) if _ACTIVE else None
        waiting = [dict(x) for x in sorted(_WAITING, key=lambda r: (r['priority'], r['seq']))]
    now = time.monotonic()
    if active:
        active['elapsed_seconds'] = round(max(0.0, now - active['started_mono']), 1)
        active.pop('started_mono', None)
    for row in waiting:
        row['wait_seconds'] = round(max(0.0, now - row['queued_mono']), 1)
        row.pop('queued_mono', None)
    return {'active': active, 'waiting': waiting}


@contextmanager
def heavy_slot(name: str, wait_seconds: float = 7200.0):
    """Priority/FIFO local heavy-task slot.

    Unlike the old non-blocking mutex, callers queue once instead of waking every
    minute and emitting `skipped-busy`. Execution training has the highest default
    priority, while a running job is never pre-empted.
    """
    global _ACTIVE
    req = {
        'name': str(name),
        'priority': _priority(str(name)),
        'seq': next(_SEQ),
        'thread': threading.current_thread().name,
        'queued_mono': time.monotonic(),
    }
    deadline = time.monotonic() + max(1.0, float(wait_seconds))
    acquired = False
    with _COND:
        _WAITING.append(req)
        while True:
            first = min(_WAITING, key=lambda r: (r['priority'], r['seq'])) if _WAITING else None
            if _ACTIVE is None and first is req:
                _WAITING.remove(req)
                _ACTIVE = {
                    'name': req['name'], 'priority': req['priority'], 'thread': req['thread'],
                    'pid': os.getpid(), 'started_mono': time.monotonic(),
                }
                acquired = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if req in _WAITING:
                    _WAITING.remove(req)
                _COND.notify_all()
                break
            _COND.wait(timeout=min(remaining, 30.0))
    try:
        yield acquired
    finally:
        if acquired:
            with _COND:
                if _ACTIVE and _ACTIVE.get('name') == req['name'] and _ACTIVE.get('thread') == req['thread']:
                    _ACTIVE = None
                _COND.notify_all()
