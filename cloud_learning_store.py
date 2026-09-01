from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any


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
        from cloud_client import get_supabase_client
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
        # V57 schema-safe inserts: old Supabase deployments may keep these JSONB
        # columns NOT NULL. Missing feeds are represented explicitly as empty objects.
        payload.setdefault("features", {})
        payload.setdefault("smart_money_data", {})
        payload.setdefault("news_data", {})
        payload.setdefault("metadata", {})
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
        if fingerprint:
            try:
                response = self.client.rpc("learning_observation_upsert_v45", {"p_row": payload}).execute()
                data = response.data
                if isinstance(data, dict) and data.get("id"):
                    return str(data["id"])
                if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("id"):
                    return str(data[0]["id"])
            except Exception:
                logger.warning("V45 observation RPC failed; verifying fingerprint before fallback", exc_info=True)
                # The server may have committed before the client observed a timeout.
                # Never blind-insert after an ambiguous RPC failure.
                try:
                    verified=self.find_by_fingerprint(fingerprint)
                    if verified and verified.get('id'):
                        return str(verified['id'])
                except Exception:
                    pass
                return None
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
        """Return oldest pending observations with pagination to prevent starvation."""
        wanted=max(1,int(limit)); page_size=min(500,wanted); rows=[]; start=0
        try:
            while len(rows) < wanted:
                take=min(page_size,wanted-len(rows))
                q=(self.client.table(self.TABLE_NAME).select("*").eq("training_status","pending")
                   .order("signal_created_at", desc=False))
                if due_only:
                    q=q.lte("resolve_after", self._now())
                chunk=list(q.range(start,start+take-1).execute().data or [])
                rows.extend(chunk)
                if len(chunk)<take:
                    break
                start += len(chunk)
            return rows
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

    def mark_terminal_outcome(self, fingerprint: str, horizon: str, reason: str) -> bool:
        """Atomically merge a terminal horizon marker in Supabase (V49)."""
        try:
            response = self.client.rpc("learning_mark_terminal_outcome_v49", {
                "p_fingerprint": str(fingerprint), "p_horizon": str(horizon), "p_reason": str(reason),
            }).execute()
            data = getattr(response, "data", response)
            return bool(data)
        except Exception:
            logger.warning("V49 terminal-outcome RPC unavailable; using metadata merge fallback")
            return self.update_outcome(str(fingerprint), {"metadata": {"terminal_outcomes": {str(horizon): str(reason)}}})

    def update_outcome(self, fingerprint: str, data: dict[str, Any]) -> bool:
        row = self.find_by_fingerprint(fingerprint)
        if not row or not row.get("id"):
            logger.warning("Learning observation не найдена: fingerprint=%s", fingerprint)
            return False
        payload = dict(data)
        if isinstance(payload.get("metadata"), dict):
            merged = dict(row.get("metadata") or {})
            incoming = dict(payload["metadata"])
            if isinstance(merged.get("terminal_outcomes"), dict) and isinstance(incoming.get("terminal_outcomes"), dict):
                terminal = dict(merged["terminal_outcomes"]); terminal.update(incoming["terminal_outcomes"]); incoming["terminal_outcomes"] = terminal
            merged.update(incoming)
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
