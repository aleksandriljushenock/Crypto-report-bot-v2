"""SQLite connection helpers with deterministic close semantics."""
from __future__ import annotations
import sqlite3
from typing import Any


class ManagedConnection(sqlite3.Connection):
    """Connection that commits/rolls back and closes when leaving ``with``.

    CPython's sqlite3.Connection context manager does not close the file handle;
    this subclass does, and also closes defensively on garbage collection.
    """
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
    kwargs.setdefault("factory", ManagedConnection)
    return sqlite3.connect(database, *args, **kwargs)
