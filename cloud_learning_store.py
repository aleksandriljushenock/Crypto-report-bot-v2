from __future__ import annotations

import logging
from typing import Any

from cloud_client import get_supabase_client


logger = logging.getLogger(__name__)


class CloudLearningStore:
    """
    Слой работы с таблицей learning_observations.

    Остальной код бота не должен напрямую выполнять
    insert, select и update через Supabase.
    """

    TABLE_NAME = "learning_observations"

    def __init__(self) -> None:
        self.client = get_supabase_client()

    def save(
        self,
        observation: dict[str, Any],
    ) -> str | None:
        """
        Сохраняет новое обучающее наблюдение.

        observation должен быть обычным словарём,
        ключи которого совпадают с колонками таблицы.
        """

        try:
            response = (
                self.client
                .table(self.TABLE_NAME)
                .insert(observation)
                .execute()
            )

            if not response.data:
                logger.warning(
                    "Supabase не вернул сохранённую запись."
                )
                return None

            observation_id = response.data[0].get("id")

            if observation_id is None:
                logger.warning(
                    "Запись создана, но поле id отсутствует."
                )
                return None

            logger.info(
                "Наблюдение сохранено: %s",
                observation_id,
            )

            return str(observation_id)

        except Exception:
            logger.exception(
                "Ошибка сохранения наблюдения в Supabase."
            )
            return None

    def unresolved(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Возвращает наблюдения, которые ещё не обработаны.
        """

        try:
            response = (
                self.client
                .table(self.TABLE_NAME)
                .select("*")
                .eq("training_status", "pending")
                .limit(limit)
                .execute()
            )

            return list(response.data or [])

        except Exception:
            logger.exception(
                "Ошибка загрузки pending-наблюдений."
            )
            return []

    def update_outcome(
        self,
        fingerprint: str,
        data: dict[str, Any],
    ) -> bool:
        """
        Находит наблюдение по metadata.fingerprint и обновляет результат.

        При создании наблюдения fingerprint должен быть сохранён
        в колонке metadata:

            metadata={"fingerprint": "..."}
        """

        if not fingerprint:
            logger.warning(
                "Невозможно обновить наблюдение: fingerprint пуст."
            )
            return False

        update_data = dict(data)
        new_metadata = update_data.pop("metadata", None)

        try:
            lookup_response = (
                self.client
                .table(self.TABLE_NAME)
                .select("id, metadata")
                .contains("metadata", {"fingerprint": fingerprint})
                .limit(1)
                .execute()
            )

            rows = list(lookup_response.data or [])

            if not rows:
                logger.warning(
                    "Наблюдение не найдено в Supabase: fingerprint=%s",
                    fingerprint,
                )
                return False

            row = rows[0]
            observation_id = row.get("id")

            if observation_id is None:
                logger.warning(
                    "У найденного наблюдения отсутствует id: fingerprint=%s",
                    fingerprint,
                )
                return False

            if new_metadata is not None:
                existing_metadata = row.get("metadata")

                if not isinstance(existing_metadata, dict):
                    existing_metadata = {}

                if not isinstance(new_metadata, dict):
                    logger.warning(
                        "Поле metadata пропущено: ожидался dict, получено %s.",
                        type(new_metadata).__name__,
                    )
                else:
                    update_data["metadata"] = {
                        **existing_metadata,
                        **new_metadata,
                    }

            response = (
                self.client
                .table(self.TABLE_NAME)
                .update(update_data)
                .eq("id", observation_id)
                .execute()
            )

            if not response.data:
                logger.warning(
                    "Supabase не вернул обновлённую запись: id=%s",
                    observation_id,
                )
                return False

            logger.info(
                "Результат наблюдения обновлён: id=%s, fingerprint=%s",
                observation_id,
                fingerprint,
            )
            return True

        except Exception:
            logger.exception(
                "Ошибка обновления результата наблюдения: fingerprint=%s",
                fingerprint,
            )
            return False

    def resolved_rows(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Return completed observations for adaptive quality learning."""
        try:
            response = (
                self.client.table(self.TABLE_NAME)
                .select("*")
                .not_.is_("real_result", "null")
                .order("created_at", desc=True)
                .limit(max(1, int(limit)))
                .execute()
            )
            return list(response.data or [])
        except Exception:
            logger.exception("Ошибка загрузки resolved-наблюдений.")
            return []

    def resolved(
        self,
        observation_id: str,
        result_data: dict[str, Any],
    ) -> bool:
        """
        Помечает наблюдение обработанным по его id.

        result_data должен содержать только колонки,
        которые реально существуют в таблице.
        """

        update_data = dict(result_data)
        update_data["training_status"] = "resolved"

        try:
            response = (
                self.client
                .table(self.TABLE_NAME)
                .update(update_data)
                .eq("id", observation_id)
                .execute()
            )

            if not response.data:
                logger.warning(
                    "Supabase не вернул обновлённую запись: id=%s",
                    observation_id,
                )
                return False

            logger.info(
                "Наблюдение помечено обработанным: %s",
                observation_id,
            )
            return True

        except Exception:
            logger.exception(
                "Ошибка обновления наблюдения %s.",
                observation_id,
            )
            return False