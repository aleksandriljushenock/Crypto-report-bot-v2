# v58.6.4 — Autonomous Watchdog / Incremental Backfill

Версия исправляет зависание автономного Execution ML на длительном `stage=backfill` и дополнительные проблемы, найденные при повторном аудите v58.6.3.

## Исправлено

1. Добавлен stage-specific watchdog: отдельные timeout для lease-wait, backfill, train и diagnose.
2. Добавлен stall watchdog для backfill/diagnose: живой PID без реального прогресса больше не может занимать pipeline до общего 3-часового timeout.
3. Backfill теперь публикует прогресс: phase, processed/total, written, skipped, unresolved, errors, elapsed и last_symbol.
4. Parent supervisor выводит backfill progress в Docker log и может отличить медленную работу от зависания.
5. Backfill стал incremental: стабильные terminal labels текущей версии не пересчитываются при каждом цикле.
6. Unresolved labels повторно проверяются по cooldown (по умолчанию 6 часов) или сразу при изменении source state на filled/no-fill.
7. Источники backfill по умолчанию читаются recent-first, чтобы новые сигналы не голодали после роста таблицы выше лимита.
8. Upsert в `execution_training_dataset_v57` выполняется batch-пакетами вместо запроса на каждую строку.
9. `learning_observations` больше не читается дважды для построения enrichment maps.
10. Переиспользуется Supabase client в paged reads вместо повторного создания клиента на каждой странице.
11. Distributed training lease больше не удерживается во время network-bound backfill; lease берётся только перед train/diagnose/publish section.
12. После stage timeout/stall worker group завершается и используется штатный retry/backoff.
13. Unsupported-symbol cache backfill стал fail-safe: symbol блокируется только если provider attempts явно подтверждают invalid/unsupported market, а не при временном network error.
14. Legacy Outcome Tracker теперь хранит terminal `outcome_failures` для unsupported/delisted рынков и не атакует их на каждом цикле.
15. `market_unavailable` отделён от настоящих `errors` в Outcome Tracker и в supervisor logs.
16. Legacy Outcome Tracker переиспользует один TradeMarketClient на цикл вместо создания клиента на каждый horizon.
17. Priority listing/discovery symbols нормализуются в canonical USDT symbol.
18. Extra symbols проходят admission gate по реальному provider universe; невалидные активы типа `SKHYNIXSTOCKUSDT` не должны попадать в deep crypto-perpetual scan после загрузки universe metadata.
19. Добавлен публичный `known_tradable_any()` для строгой проверки symbol против последнего provider universe.
20. Execution model bundle bump: schema 5864, prefix `execution-ensemble-v58.6.4-*`.

## Новые параметры

- `EXECUTION_STAGE_LEASE_TIMEOUT_SECONDS=300`
- `EXECUTION_STAGE_BACKFILL_TIMEOUT_SECONDS=1800`
- `EXECUTION_STAGE_BACKFILL_STALL_SECONDS=300`
- `EXECUTION_STAGE_TRAIN_TIMEOUT_SECONDS=9000`
- `EXECUTION_STAGE_DIAGNOSE_TIMEOUT_SECONDS=900`
- `EXECUTION_STAGE_DIAGNOSE_STALL_SECONDS=600`
- `EXECUTION_BACKFILL_UNRESOLVED_RETRY_HOURS=6`
- `EXECUTION_BACKFILL_UPSERT_BATCH_SIZE=100`
- `EXECUTION_BACKFILL_RECENT_FIRST=true`

## Ресурсы VPS

Профиль v58.6.3 сохранён: 4 vCPU, Docker CPU 4.0, hard memory 6 GiB, adaptive memory guard, 4 ML workers.
