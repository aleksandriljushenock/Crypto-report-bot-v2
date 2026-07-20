from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from cloud_client import get_supabase_client

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


class CloudModelStore:
    """Persistent Supabase registry for v14 models and training runs.

    The writer supports both the new recommended schema and the compact
    schema used by the legacy train_cloud.py job.
    """

    MODEL_TABLE = "model_registry"
    RUN_TABLE = "training_runs"

    def __init__(self) -> None:
        self.client = get_supabase_client()

    def save_training_run(self, result: dict[str, Any]) -> bool:
        version = result.get("version") or result.get("active") or "collecting-data"
        metrics = result.get("metrics") or {}
        samples = int(result.get("samples") or 0)
        status = str(result.get("status") or "unknown")
        candidates = [
            {
                "model_version": version,
                "metrics": metrics,
                "feature_importance": {},
                "samples_count": samples,
                "status": status,
                "summary": result,
                "created_at": _now(),
            },
            {
                "model_version": version,
                "metrics": metrics,
                "feature_importance": {},
                "samples_count": samples,
                "status": status,
            },
            {
                "version": version,
                "status": status,
                "summary_json": result,
                "sample_count": samples,
                "created_at": _now(),
            },
        ]
        return self._insert_compatible(self.RUN_TABLE, candidates)

    def save_model(self, model: dict[str, Any], status: str, sample_count: int) -> bool:
        version = str(model.get("version") or "unknown")
        config = model.get("config") or {}
        metrics = model.get("metrics") or {}
        active = status == "active"
        if active:
            self._retire_active_models(version)
        candidates = [
            {
                "model_version": version,
                "status": status,
                "config": config,
                "metrics": metrics,
                "samples_count": int(sample_count),
                "is_active": active,
                "created_at": _now(),
                "activated_at": _now() if active else None,
            },
            {
                "version": version,
                "status": status,
                "config_json": config,
                "metrics_json": metrics,
                "sample_count": int(sample_count),
                "created_at": _now(),
                "activated_at": _now() if active else None,
            },
            {
                "model_version": version,
                "status": status,
                "parameters": config,
                "metrics": metrics,
                "samples_count": int(sample_count),
            },
        ]
        return self._upsert_compatible(self.MODEL_TABLE, candidates)

    def load_active_model(self) -> dict[str, Any] | None:
        queries = [
            lambda: self.client.table(self.MODEL_TABLE).select("*").eq("is_active", True).order("created_at", desc=True).limit(1).execute(),
            lambda: self.client.table(self.MODEL_TABLE).select("*").eq("status", "active").order("created_at", desc=True).limit(1).execute(),
            lambda: self.client.table(self.MODEL_TABLE).select("*").eq("status", "champion").order("created_at", desc=True).limit(1).execute(),
        ]
        for query in queries:
            try:
                rows = list(query().data or [])
                if rows:
                    return self._normalize_model(rows[0])
            except Exception:
                continue
        return None

    def list_models(self, limit: int = 8) -> list[dict[str, Any]]:
        try:
            response = self.client.table(self.MODEL_TABLE).select("*").order("created_at", desc=True).limit(limit).execute()
            return [self._normalize_model(row) for row in (response.data or [])]
        except Exception:
            logger.exception("Не удалось загрузить model_registry из Supabase")
            return []

    def _normalize_model(self, row: dict[str, Any]) -> dict[str, Any]:
        version = row.get("model_version") or row.get("version")
        config = _dict(row.get("config") or row.get("config_json") or row.get("parameters"))
        metrics = _dict(row.get("metrics") or row.get("metrics_json"))
        return {
            "version": version,
            "status": row.get("status") or ("active" if row.get("is_active") else "challenger"),
            "config": config,
            "weights": config.get("global_weights") or config.get("weights") or {},
            "metrics": metrics,
            "sample_count": row.get("samples_count") or row.get("sample_count") or 0,
            "created_at": row.get("created_at"),
            "activated_at": row.get("activated_at"),
            "rules": config.get("rules") or [],
        }

    def _retire_active_models(self, keep_version: str) -> None:
        for active_col, version_col in (("is_active", "model_version"), ("status", "version"), ("status", "model_version")):
            try:
                update = {"is_active": False, "status": "retired"} if active_col == "is_active" else {"status": "retired"}
                q = self.client.table(self.MODEL_TABLE).update(update)
                q = q.eq(active_col, True if active_col == "is_active" else "active")
                q.neq(version_col, keep_version).execute()
                return
            except Exception:
                continue

    def _insert_compatible(self, table: str, candidates: list[dict[str, Any]]) -> bool:
        last = None
        for payload in candidates:
            try:
                self.client.table(table).insert(payload).execute()
                return True
            except Exception as exc:
                last = exc
        logger.error("Supabase insert failed for %s: %s", table, last)
        return False

    def _upsert_compatible(self, table: str, candidates: list[dict[str, Any]]) -> bool:
        last = None
        for payload in candidates:
            try:
                key = "model_version" if "model_version" in payload else "version"
                self.client.table(table).upsert(payload, on_conflict=key).execute()
                return True
            except Exception as exc:
                last = exc
                try:
                    self.client.table(table).insert(payload).execute()
                    return True
                except Exception as exc2:
                    last = exc2
        logger.error("Supabase upsert failed for %s: %s", table, last)
        return False
