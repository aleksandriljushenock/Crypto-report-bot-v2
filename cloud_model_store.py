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


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


class CloudModelStore:
    """Persistent Supabase storage for v14 learning models.

    This implementation intentionally targets the actual Supabase schema used
    by this project instead of trying several incompatible payload formats.
    """

    MODEL_TABLE = "model_registry"
    RUN_TABLE = "training_runs"
    DEFAULT_MODEL_NAME = "learning-engine-v14"

    def __init__(self) -> None:
        self.client = get_supabase_client()

    def save_training_run(self, result: dict[str, Any]) -> bool:
        version = str(
            result.get("model_version")
            or result.get("version")
            or result.get("active")
            or "collecting-data"
        )
        model_name = str(result.get("model_name") or self.DEFAULT_MODEL_NAME)
        status = str(result.get("status") or "unknown")
        metrics = _dict(result.get("metrics"))
        parameters = _dict(result.get("parameters") or result.get("config"))
        feature_names = _list(result.get("feature_names"))

        samples_total = _int(
            result.get("samples_total")
            or result.get("sample_count")
            or result.get("samples_count")
            or result.get("samples")
        )
        samples_train = _int(result.get("samples_train"))
        samples_validation = _int(result.get("samples_validation"))

        started_at = result.get("started_at") or _now()
        completed_at = result.get("completed_at")
        if completed_at is None and status.lower() in {
            "completed",
            "complete",
            "success",
            "succeeded",
            "failed",
            "error",
        }:
            completed_at = _now()

        payload = {
            "status": status,
            "model_name": model_name,
            "model_version": version,
            "algorithm": result.get("algorithm"),
            "target_name": result.get("target_name"),
            "samples_total": samples_total,
            "samples_train": samples_train,
            "samples_validation": samples_validation,
            "metrics": metrics,
            "feature_names": feature_names,
            "parameters": parameters,
            "metadata": result,
            "model_storage_path": result.get("model_storage_path")
            or result.get("storage_path"),
            "error_message": result.get("error_message") or result.get("error"),
            "started_at": started_at,
            "completed_at": completed_at,
            "created_at": result.get("created_at") or _now(),
        }
        payload = {key: value for key, value in payload.items() if value is not None}

        try:
            self.client.table(self.RUN_TABLE).insert(payload).execute()
            return True
        except Exception:
            logger.exception(
                "Supabase insert failed for %s; model=%s version=%s",
                self.RUN_TABLE,
                model_name,
                version,
            )
            return False

    def save_model(self, model: dict[str, Any], status: str, sample_count: int) -> bool:
        version = str(model.get("model_version") or model.get("version") or "unknown")
        model_name = str(
            model.get("model_name")
            or model.get("name")
            or self.DEFAULT_MODEL_NAME
        )
        config = _dict(model.get("config") or model.get("parameters"))
        metrics = _dict(model.get("metrics"))
        feature_names = _list(model.get("feature_names"))
        active = str(status).lower() in {"active", "champion"}
        now = _now()

        payload = {
            "model_name": model_name,
            "model_version": version,
            "storage_bucket": model.get("storage_bucket") or "models",
            "storage_path": model.get("storage_path")
            or model.get("model_storage_path"),
            "algorithm": model.get("algorithm"),
            "framework": model.get("framework"),
            "metrics": metrics,
            "feature_names": feature_names,
            "metadata": {
                **_dict(model.get("metadata")),
                "status": status,
                "sample_count": _int(sample_count),
                "parameters": config,
            },
            "training_run_id": model.get("training_run_id"),
            "is_active": active,
            "created_at": model.get("created_at") or now,
            "activated_at": now if active else model.get("activated_at"),
            # Compatibility columns already added to the current database.
            "status": status,
            "parameters": config,
            "sample_count": _int(sample_count),
            "samples_count": _int(sample_count),
        }
        payload = {key: value for key, value in payload.items() if value is not None}

        try:
            existing = (
                self.client.table(self.MODEL_TABLE)
                .select("id")
                .eq("model_name", model_name)
                .eq("model_version", version)
                .limit(1)
                .execute()
            )
            rows = list(existing.data or [])

            if rows:
                model_id = rows[0]["id"]
                update_payload = dict(payload)
                update_payload.pop("created_at", None)
                self.client.table(self.MODEL_TABLE).update(update_payload).eq(
                    "id", model_id
                ).execute()
            else:
                self.client.table(self.MODEL_TABLE).insert(payload).execute()

            if active:
                self._retire_other_active_models(model_name, version)
            return True
        except Exception:
            logger.exception(
                "Supabase save failed for %s; model=%s version=%s",
                self.MODEL_TABLE,
                model_name,
                version,
            )
            return False

    def load_active_model(self) -> dict[str, Any] | None:
        queries = [
            lambda: self.client.table(self.MODEL_TABLE)
            .select("*")
            .eq("is_active", True)
            .order("activated_at", desc=True)
            .order("created_at", desc=True)
            .limit(1)
            .execute(),
            lambda: self.client.table(self.MODEL_TABLE)
            .select("*")
            .eq("status", "active")
            .order("created_at", desc=True)
            .limit(1)
            .execute(),
            lambda: self.client.table(self.MODEL_TABLE)
            .select("*")
            .eq("status", "champion")
            .order("created_at", desc=True)
            .limit(1)
            .execute(),
        ]
        for query in queries:
            try:
                rows = list(query().data or [])
                if rows:
                    return self._normalize_model(rows[0])
            except Exception:
                logger.debug("Active model query variant failed", exc_info=True)
        return None

    def list_models(self, limit: int = 8) -> list[dict[str, Any]]:
        try:
            response = (
                self.client.table(self.MODEL_TABLE)
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return [self._normalize_model(row) for row in (response.data or [])]
        except Exception:
            logger.exception("Не удалось загрузить model_registry из Supabase")
            return []

    def _normalize_model(self, row: dict[str, Any]) -> dict[str, Any]:
        metadata = _dict(row.get("metadata"))
        config = _dict(
            row.get("parameters")
            or metadata.get("parameters")
            or row.get("config")
            or row.get("config_json")
        )
        metrics = _dict(row.get("metrics") or row.get("metrics_json"))
        sample_count = (
            row.get("sample_count")
            or row.get("samples_count")
            or metadata.get("sample_count")
            or 0
        )
        return {
            "model_name": row.get("model_name") or self.DEFAULT_MODEL_NAME,
            "version": row.get("model_version") or row.get("version"),
            "status": row.get("status")
            or metadata.get("status")
            or ("active" if row.get("is_active") else "challenger"),
            "config": config,
            "weights": config.get("global_weights") or config.get("weights") or {},
            "metrics": metrics,
            "sample_count": _int(sample_count),
            "created_at": row.get("created_at"),
            "activated_at": row.get("activated_at"),
            "rules": config.get("rules") or [],
        }

    def _retire_other_active_models(self, model_name: str, keep_version: str) -> None:
        """Retire all other active versions after the new model was saved."""
        try:
            query = (
                self.client.table(self.MODEL_TABLE)
                .update({"is_active": False, "status": "retired"})
                .eq("model_name", model_name)
                .eq("is_active", True)
                .neq("model_version", keep_version)
            )
            query.execute()
        except Exception:
            # Saving the new model is more important than retirement cleanup.
            logger.warning(
                "Не удалось перевести старые модели в retired: model=%s keep=%s",
                model_name,
                keep_version,
                exc_info=True,
            )