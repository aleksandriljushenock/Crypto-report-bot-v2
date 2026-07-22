"""Persistent checkpoints for AI Self Learning MAX v14.

The local SQLite database lives on Render's ephemeral disk. This module mirrors
it to Supabase Storage and restores the newest valid copy after a restart.
Supabase observations and model_registry remain the source of truth; the SQLite
checkpoint preserves local calibration bins, rules, drift snapshots and run
history so the process can resume without rebuilding everything.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BUCKET = os.getenv("LEARNING_CHECKPOINT_BUCKET", "learning-checkpoints")
OBJECT_PATH = os.getenv("LEARNING_CHECKPOINT_PATH", "v14/latest/learning_v14.db")
META_PATH = os.getenv("LEARNING_CHECKPOINT_META_PATH", "v14/latest/metadata.json")
ENABLED = os.getenv("LEARNING_CHECKPOINT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
MAX_LOCAL_BACKUPS = max(1, int(os.getenv("LEARNING_CHECKPOINT_LOCAL_BACKUPS", "3")))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storage():
    from cloud_client import get_supabase_client
    return get_supabase_client().storage.from_(BUCKET)


def _download_bytes(path: str) -> bytes:
    data = _storage().download(path)
    if isinstance(data, bytes):
        return data
    if hasattr(data, "content"):
        return bytes(data.content)
    return bytes(data)


def _upload_bytes(path: str, data: bytes, content_type: str) -> None:
    storage = _storage()
    options = {"content-type": content_type, "upsert": "true"}
    try:
        storage.upload(path, data, options)
    except Exception:
        # supabase-py versions differ: update() is the most reliable fallback.
        try:
            storage.update(path, data, {"content-type": content_type})
        except Exception:
            storage.remove([path])
            storage.upload(path, data, {"content-type": content_type})


def _valid_sqlite(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 512:
        return False
    try:
        conn = sqlite3.connect(path)
        result = conn.execute("PRAGMA integrity_check").fetchone()
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        return bool(result and result[0] == "ok" and "model_versions" in tables)
    except Exception:
        return False


def restore_checkpoint(db_path: Path) -> dict[str, Any]:
    """Restore the newest cloud checkpoint only when the local DB is absent/invalid."""
    if not ENABLED:
        return {"status": "disabled"}
    db_path = Path(db_path)
    if _valid_sqlite(db_path):
        return {"status": "local-valid", "path": str(db_path)}
    try:
        payload = _download_bytes(OBJECT_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=db_path.parent, delete=False, suffix=".restore") as tmp:
            tmp.write(payload)
            temp_path = Path(tmp.name)
        if not _valid_sqlite(temp_path):
            temp_path.unlink(missing_ok=True)
            raise RuntimeError("downloaded checkpoint failed SQLite integrity check")
        os.replace(temp_path, db_path)
        logger.info("Learning checkpoint restored: %s bytes -> %s", len(payload), db_path)
        return {"status": "restored", "bytes": len(payload), "path": str(db_path)}
    except Exception as exc:
        logger.warning("Learning checkpoint restore skipped: %s", exc)
        return {"status": "not-restored", "error": str(exc)}


def save_checkpoint(db_path: Path, reason: str = "periodic") -> dict[str, Any]:
    """Create a transaction-consistent SQLite backup and upload it to Supabase."""
    if not ENABLED:
        return {"status": "disabled"}
    db_path = Path(db_path)
    if not db_path.exists():
        return {"status": "missing", "path": str(db_path)}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.parent / f"learning_v14-{stamp}.db"
    try:
        source = sqlite3.connect(db_path, timeout=30)
        target = sqlite3.connect(backup_path)
        with target:
            source.backup(target)
        target.close()
        source.close()
        if not _valid_sqlite(backup_path):
            raise RuntimeError("local checkpoint failed SQLite integrity check")

        payload = backup_path.read_bytes()
        _upload_bytes(OBJECT_PATH, payload, "application/x-sqlite3")
        metadata = {
            "created_at": _now(),
            "reason": reason,
            "size_bytes": len(payload),
            "object_path": OBJECT_PATH,
            "schema": "learning-engine-v14",
        }
        _upload_bytes(META_PATH, json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")

        old = sorted(db_path.parent.glob("learning_v14-*.db"), reverse=True)
        for path in old[MAX_LOCAL_BACKUPS:]:
            path.unlink(missing_ok=True)
        logger.info("Learning checkpoint saved: reason=%s bytes=%s", reason, len(payload))
        return {"status": "saved", **metadata}
    except Exception as exc:
        logger.exception("Learning checkpoint save failed")
        return {"status": "failed", "error": str(exc)}
    finally:
        if backup_path.exists() and backup_path not in sorted(db_path.parent.glob("learning_v14-*.db"), reverse=True)[:MAX_LOCAL_BACKUPS]:
            backup_path.unlink(missing_ok=True)
