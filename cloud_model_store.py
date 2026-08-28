from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from cloud_client import get_supabase_client

logger = logging.getLogger(__name__)

STORE_BUILD = "2026-07-21-quality-upgrade-1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


class CloudModelStore:
    """Supabase persistence aligned with the current project schema.

    The class deliberately writes only columns that exist in the supplied
    `training_runs` and `model_registry` tables. Model parameters are stored
    inside `model_registry.metadata`, so no compatibility-only columns are
    required.
    """

    MODEL_TABLE = "model_registry"
    RUN_TABLE = "training_runs"
    DEFAULT_MODEL_NAME = "learning-engine-v14"

    def __init__(self) -> None:
        self.client = get_supabase_client()
        self._known_run_statuses: list[str] | None = None
        logger.info("CloudModelStore loaded; build=%s", STORE_BUILD)

    def save_training_run(self, result: dict[str, Any]) -> bool:
        model_name = str(result.get("model_name") or self.DEFAULT_MODEL_NAME)
        version = str(
            result.get("model_version")
            or result.get("version")
            or result.get("active")
            or "collecting-data"
        )

        raw_status = str(result.get("status") or "completed").strip().lower()
        normalized_status = self._normalize_requested_status(raw_status)
        terminal = normalized_status in {"completed", "failed"}

        payload: dict[str, Any] = {
            "model_name": model_name,
            "model_version": version,
            "algorithm": str(result.get("algorithm") or "learning-engine-v14"),
            "target_name": str(result.get("target_name") or "signal_outcome"),
            "samples_total": _as_int(
                result.get("samples_total")
                or result.get("sample_count")
                or result.get("samples_count")
                or result.get("samples")
            ),
            "samples_train": _as_int(result.get("samples_train")),
            "samples_validation": _as_int(result.get("samples_validation")),
            "metrics": _as_dict(result.get("metrics")),
            "feature_names": _as_list(result.get("feature_names")),
            "parameters": _as_dict(result.get("parameters") or result.get("config")),
            "metadata": result,
            "model_storage_path": result.get("model_storage_path") or result.get("storage_path"),
            "error_message": result.get("error_message") or result.get("error"),
            "started_at": result.get("started_at") or _now(),
            "completed_at": result.get("completed_at") or (_now() if terminal else None),
            "created_at": result.get("created_at") or _now(),
        }
        payload = {key: value for key, value in payload.items() if value is not None}

        errors: list[str] = []
        for status in self._run_status_candidates(raw_status):
            candidate = dict(payload)
            if status is not None:
                candidate["status"] = status
            try:
                self.client.table(self.RUN_TABLE).insert(candidate).execute()
                if status is None:
                    logger.warning(
                        "training_runs saved using database default status; model=%s version=%s",
                        model_name,
                        version,
                    )
                elif status != raw_status:
                    logger.warning(
                        "training_runs saved with compatible status=%s instead of %s; model=%s version=%s",
                        status,
                        raw_status,
                        model_name,
                        version,
                    )
                return True
            except Exception as exc:
                errors.append(_error_text(exc))

        logger.error(
            "Supabase insert failed for training_runs; model=%s version=%s; attempts=%s",
            model_name,
            version,
            " | ".join(errors),
        )
        return False

    def save_model(self, model: dict[str, Any], status: str, sample_count: int) -> bool:
        model_name = str(model.get("model_name") or model.get("name") or self.DEFAULT_MODEL_NAME)
        version = str(model.get("model_version") or model.get("version") or "unknown")
        status_text = str(status or "challenger").strip().lower()
        active = status_text in {"active", "champion"}
        config = _as_dict(model.get("config") or model.get("parameters"))
        now = _now()

        metadata = _as_dict(model.get("metadata"))
        metadata.update(
            {
                "status": status_text,
                "sample_count": _as_int(sample_count),
                "parameters": config,
                "store_build": STORE_BUILD,
            }
        )

        payload: dict[str, Any] = {
            "model_name": model_name,
            "model_version": version,
            "storage_bucket": str(model.get("storage_bucket") or "models"),
            "storage_path": str(
                model.get("storage_path")
                or model.get("model_storage_path")
                or f"inline/{model_name}/{version}.json"
            ),
            "algorithm": str(model.get("algorithm") or "rule-ensemble"),
            "framework": str(model.get("framework") or "python"),
            "metrics": _as_dict(model.get("metrics")),
            "feature_names": _as_list(model.get("feature_names")),
            "metadata": metadata,
            "training_run_id": model.get("training_run_id"),
            "is_active": active,
            "created_at": model.get("created_at") or now,
            "activated_at": now if active else model.get("activated_at"),
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
                update_payload = dict(payload)
                update_payload.pop("created_at", None)
                (
                    self.client.table(self.MODEL_TABLE)
                    .update(update_payload)
                    .eq("id", rows[0]["id"])
                    .execute()
                )
            else:
                self.client.table(self.MODEL_TABLE).insert(payload).execute()

            if active:
                self._retire_other_active_models(model_name, version)
            return True
        except Exception as exc:
            logger.error(
                "Supabase save failed for model_registry; model=%s version=%s; payload_keys=%s; error=%s",
                model_name,
                version,
                sorted(payload.keys()),
                _error_text(exc),
                exc_info=True,
            )
            return False

    def promote_version_atomic(self, model_name: str, version: str) -> bool:
        """Atomically make exactly one cloud V14 version active."""
        try:
            data = self.client.rpc("model_registry_promote_v47", {"p_model_name": str(model_name), "p_model_version": str(version)}).execute().data
            return bool(data)
        except Exception:
            logger.exception("Atomic cloud model promotion failed: %s/%s", model_name, version)
            return False

    def load_active_model(self) -> dict[str, Any] | None:
        try:
            response = (
                self.client.table(self.MODEL_TABLE)
                .select("*")
                .eq("is_active", True)
                .order("activated_at", desc=True)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = list(response.data or [])
            return self._normalize_model(rows[0]) if rows else None
        except Exception:
            logger.exception("Не удалось загрузить активную модель из Supabase")
            return None

    def list_models(self, limit: int = 8) -> list[dict[str, Any]]:
        try:
            response = (
                self.client.table(self.MODEL_TABLE)
                .select("*")
                .order("created_at", desc=True)
                .limit(max(1, _as_int(limit, 8)))
                .execute()
            )
            return [self._normalize_model(row) for row in (response.data or [])]
        except Exception:
            logger.exception("Не удалось загрузить model_registry из Supabase")
            return []

    def _normalize_model(self, row: dict[str, Any]) -> dict[str, Any]:
        metadata = _as_dict(row.get("metadata"))
        config = _as_dict(metadata.get("parameters"))
        return {
            "model_name": row.get("model_name") or self.DEFAULT_MODEL_NAME,
            "version": row.get("model_version"),
            "status": metadata.get("status") or ("active" if row.get("is_active") else "challenger"),
            "config": config,
            "weights": config.get("global_weights") or config.get("weights") or {},
            "metrics": _as_dict(row.get("metrics")),
            "sample_count": _as_int(metadata.get("sample_count")),
            "created_at": row.get("created_at"),
            "activated_at": row.get("activated_at"),
            "rules": config.get("rules") or [],
        }

    def _run_status_candidates(self, requested: str) -> list[str | None]:
        normalized = self._normalize_requested_status(requested)
        candidates: list[str | None] = [normalized]

        for existing in self._load_known_run_statuses():
            if existing not in candidates:
                candidates.append(existing)

        # Omitting the field lets PostgreSQL use the schema's own valid default.
        candidates.append(None)

        for fallback in ("completed", "success", "running", "started", "pending", "failed"):
            if fallback not in candidates:
                candidates.append(fallback)
        return candidates

    def _load_known_run_statuses(self) -> list[str]:
        if self._known_run_statuses is not None:
            return self._known_run_statuses
        try:
            response = self.client.table(self.RUN_TABLE).select("status").limit(100).execute()
            values: list[str] = []
            for row in response.data or []:
                value = row.get("status")
                if isinstance(value, str) and value and value not in values:
                    values.append(value)
            self._known_run_statuses = values
        except Exception:
            logger.debug("Не удалось прочитать существующие training_runs.status", exc_info=True)
            self._known_run_statuses = []
        return self._known_run_statuses

    @staticmethod
    def _normalize_requested_status(value: str) -> str:
        raw = value.strip().lower()
        if raw in {"complete", "success", "succeeded", "trained", "done", "ok"}:
            return "completed"
        if raw in {"failure", "error", "errored"}:
            return "failed"
        if raw in {"queued", "waiting"}:
            return "pending"
        if raw in {"collecting-data", "collecting", "training", "started", "in-progress", "processing"}:
            return "running"
        if raw in {"active", "champion", "challenger"}:
            return "completed"
        return raw or "pending"

    def _retire_other_active_models(self, model_name: str, keep_version: str) -> None:
        try:
            (
                self.client.table(self.MODEL_TABLE)
                .update({"is_active": False})
                .eq("model_name", model_name)
                .eq("is_active", True)
                .neq("model_version", keep_version)
                .execute()
            )
        except Exception:
            logger.warning(
                "Не удалось деактивировать предыдущие модели: model=%s keep=%s",
                model_name,
                keep_version,
                exc_info=True,
            )
