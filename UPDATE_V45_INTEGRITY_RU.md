# V45.0.0 — Integrity & Learning Correctness

V45 исправляет дефекты, найденные при повторном аудите V44.

Основные изменения:
- learning observation использует фактическую рыночную цену сигнала; плановый entry хранится отдельно;
- при невозможности получить фактическую цену observation сохраняется, но не используется для outcome learning до появления корректного baseline;
- Learning MAX Feature Store получает реальные matured outcomes;
- Alpha и Shadow outcomes используют исторические завершённые свечи, а не текущий ticker;
- Shadow: строгая нормализация LONG/SHORT и fill-time на закрытии свечи;
- V14: breakeven = neutral target 0.5, promotion оценивает фактическую specialist routing;
- Adaptive model исключает breakeven из binary training;
- Adaptive promotion использует `adaptive_model_compare_promote_v45` с expected champion под advisory lock;
- model training coordinator использует thread lock + `fcntl.flock` между процессами;
- изменение manual/bounded weight инвалидирует calibration до следующего обучения;
- ручная активация V14 синхронизируется с CloudModelStore;
- cloud persistence failure V14 возвращается как `cloud_sync=degraded`;
- CloudLearningStore pending queue пагинируется от старых записей;
- initial cloud save можно восстановить idempotent RPC `learning_observation_upsert_v45`;
- V45 migration сначала удаляет fingerprint duplicates и только затем создаёт UNIQUE INDEX;
- Telegram polling сохраняет обработанные update_id после успешной обработки;
- Telegram HTML splitter сохраняет баланс поддерживаемых HTML tags;
- Paper ledger repair throttled (`PAPER_LEDGER_REPAIR_INTERVAL_SECONDS`, default 900);
- Paper statistics default safe window = 10000 закрытых trades;
- Strategy Lab считает переход подтверждённым только после успешного repository update;
- Automation Supervisor сохраняет last success/error для background workers;
- legacy training entrypoints используют общий coordinator;
- ручной большой report пишет subprocess output во временный файл вместо удержания всего stdout/stderr в RAM.

## Обязательная миграция

После V43/V44 выполнить:

`migrations/SUPABASE_V45_INTEGRITY.sql`

Она добавляет:
- dedupe learning observations по fingerprint;
- unique fingerprint index;
- `learning_observation_upsert_v45`;
- `adaptive_model_compare_promote_v45` с advisory lock и compare-and-promote.

RPC доступны только `service_role`.

## Ограничение OHLC execution

Точный порядок событий внутри boundary 1m candle невозможно восстановить из OHLC. V45 сохраняет консервативное поведение: неоднозначная boundary candle не используется для TP/SL/entry wick inference. Для свежих Paper positions используется минимально доступная 1m granularity. Это предотвращает выдуманный event order, но оставляет максимум одну минуту неопределённости, если нет trade-level data.
