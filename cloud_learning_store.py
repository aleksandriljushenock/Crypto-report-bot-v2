from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from cloud_client import get_supabase_client

logger = logging.getLogger(__name__)


class CloudLearningStore:
    """Cloud-first repository for ``learning_observations``.

    Supabase is the durable source of truth. Local SQLite databases are caches
    that may be rebuilt after every Render restart.
    """

    TABLE_NAME = "learning_observations"
    ALLOWED_COLUMNS = {
        "id", "symbol", "timeframe", "signal_type", "signal_direction",
        "signal_score", "signal_confidence", "entry_price", "target_price",
        "stop_loss", "market_price_at_signal", "market_price_after",
        "price_change_pct", "max_favorable_excursion_pct",
        "max_adverse_excursion_pct", "outcome", "outcome_score", "features",
        "smart_money_data", "news_data", "metadata", "signal_created_at",
        "resolve_after", "resolved_at", "training_status", "training_run_id",
        "created_at", "updated_at", "real_result",
        "quality_score", "calibrated_probability", "expected_value_pct",
        "quality_decision", "hedge_profile_version",
        "chronos_probability", "chronos_return_pct", "chronos_agreement",
        "chronos_model", "chronos_status",
    }


    HEDGE_COLUMNS = {
        "quality_score", "calibrated_probability", "expected_value_pct",
        "quality_decision", "hedge_profile_version",
        "chronos_probability", "chronos_return_pct", "chronos_agreement",
        "chronos_model", "chronos_status",
    }

    @classmethod
    def _legacy_compatible(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """Keep hedge metrics in metadata when DB migration is not applied yet."""
        result = dict(payload)
        hedge = {key: result.pop(key) for key in list(cls.HEDGE_COLUMNS) if key in result}
        if hedge:
            metadata = dict(result.get("metadata") or {})
            metadata.update(hedge)
            result["metadata"] = metadata
        return result

    def __init__(self) -> None:
        self.client = get_supabase_client()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _clean(cls, payload: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in payload.items() if k in cls.ALLOWED_COLUMNS and v is not None}

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            return str(metadata.get("fingerprint") or "")
        return ""

    def find_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        if not fingerprint:
            return None
        try:
            response = (
                self.client.table(self.TABLE_NAME)
                .select("*")
                .contains("metadata", {"fingerprint": fingerprint})
                .limit(1)
                .execute()
            )
            rows = list(response.data or [])
            return rows[0] if rows else None
        except Exception as exc:
            logger.exception("Ошибка поиска learning observation: fingerprint=%s", fingerprint)
            raise RuntimeError("learning observation lookup failed") from exc

    def save(self, observation: dict[str, Any]) -> str | None:
        """Idempotently create or merge an observation by fingerprint."""
        payload = self._clean(dict(observation))
        payload.setdefault("training_status", "pending")
        payload["updated_at"] = self._now()
        fingerprint = self._fingerprint(payload)
        existing = self.find_by_fingerprint(fingerprint) if fingerprint else None
        if existing and existing.get("id"):
            merged_metadata = dict(existing.get("metadata") or {})
            merged_metadata.update(payload.get("metadata") or {})
            payload["metadata"] = merged_metadata
            try:
                response = (
                    self.client.table(self.TABLE_NAME)
                    .update(payload)
                    .eq("id", existing["id"])
                    .execute()
                )
            except Exception:
                logger.warning("Hedge columns unavailable; retrying observation update via metadata")
                try:
                    response = (
                        self.client.table(self.TABLE_NAME)
                        .update(self._legacy_compatible(payload))
                        .eq("id", existing["id"])
                        .execute()
                    )
                except Exception:
                    logger.exception("Ошибка сохранения learning observation")
                    return None
            return str(existing["id"]) if response.data else None

        payload.setdefault("created_at", self._now())
        try:
            response = self.client.table(self.TABLE_NAME).insert(payload).execute()
        except Exception:
            logger.warning("Hedge columns unavailable; retrying observation insert via metadata")
            try:
                response = self.client.table(self.TABLE_NAME).insert(self._legacy_compatible(payload)).execute()
            except Exception:
                logger.exception("Ошибка сохранения learning observation")
                return None
        if not response.data:
            logger.warning("Supabase не вернул сохранённую learning observation")
            return None
        return str(response.data[0].get("id")) if response.data[0].get("id") else None

    def pending(self, limit: int = 1000, due_only: bool = False) -> list[dict[str, Any]]:
        """Return pending observations, optionally only those whose first horizon is due."""
        try:
            query = (
                self.client.table(self.TABLE_NAME)
                .select("*")
                .eq("training_status", "pending")
                .order("signal_created_at", desc=False)
                .limit(max(1, int(limit)))
            )
            if due_only:
                query = query.lte("resolve_after", self._now())
            return list(query.execute().data or [])
        except Exception:
            logger.exception("Ошибка загрузки pending learning observations")
            return []

    # Backward-compatible alias.
    unresolved = pending

    def update_by_id(self, observation_id: str, data: dict[str, Any]) -> bool:
        payload = self._clean(dict(data))
        payload["updated_at"] = self._now()
        try:
            response = (
                self.client.table(self.TABLE_NAME)
                .update(payload)
                .eq("id", observation_id)
                .execute()
            )
            return bool(response.data)
        except Exception:
            logger.warning("Hedge columns unavailable; retrying id update via metadata")
            try:
                response = (
                    self.client.table(self.TABLE_NAME)
                    .update(self._legacy_compatible(payload))
                    .eq("id", observation_id)
                    .execute()
                )
                return bool(response.data)
            except Exception:
                logger.exception("Ошибка обновления learning observation id=%s", observation_id)
                return False

    def update_outcome(self, fingerprint: str, data: dict[str, Any]) -> bool:
        row = self.find_by_fingerprint(fingerprint)
        if not row or not row.get("id"):
            logger.warning("Learning observation не найдена: fingerprint=%s", fingerprint)
            return False
        payload = dict(data)
        if isinstance(payload.get("metadata"), dict):
            merged = dict(row.get("metadata") or {})
            merged.update(payload["metadata"])
            payload["metadata"] = merged
        return self.update_by_id(str(row["id"]), payload)

    def resolved_rows(self, limit: int = 3000) -> list[dict[str, Any]]:
        """Return all durable samples that contain at least one resolved horizon."""
        try:
            response = (
                self.client.table(self.TABLE_NAME)
                .select("*")
                .not_.is_("real_result", "null")
                .order("signal_created_at", desc=True)
                .limit(max(1, int(limit)))
                .execute()
            )
            rows = list(response.data or [])
            rows.reverse()  # newest LIMIT, chronological order for training
            return rows
        except Exception:
            logger.exception("Ошибка загрузки resolved learning observations")
            return []

    def resolved(self, observation_id: str, result_data: dict[str, Any]) -> bool:
        payload = dict(result_data)
        payload.setdefault("training_status", "ready")
        payload.setdefault("resolved_at", self._now())
        return self.update_by_id(observation_id, payload)
