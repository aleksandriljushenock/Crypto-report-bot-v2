# v58.6.3 — Orchestrated Autonomous Learning

Цель версии — устранить starvation автономного execution ML, сделать тяжёлые задачи наблюдаемыми и прекратить шторм запросов Outcome Tracker по устаревшим/некорректным символам.

## Исправленные проблемы

1. Устранён общий non-blocking mutex, из-за которого `trade-outcome-tracker` и другие долгие I/O задачи блокировали execution training.
2. Устранена гонка первого запуска: `trade-outcome-tracker` и execution trainer стартовали одновременно примерно через 120 секунд, и первый систематически забирал lock.
3. Тяжёлые ML-задачи теперь используют локальную priority/FIFO очередь. Execution trainer имеет высший приоритет среди ожидающих задач.
4. Ожидающие heavy-задачи больше не просыпаются каждую минуту с `skipped because another heavy task is running`; они стоят в очереди и логируют владельца один раз.
5. Сетевые/IO задачи вынесены в отдельный semaphore и больше не удерживают training slot.
6. В runtime status добавлены `heavyQueue` и `trainingLease` с owner/PID/elapsed/waiting.
7. Все production-вызовы distributed `training_slot` получили понятный owner; owner теперь включает host, PID и имя задачи.
8. Execution auto worker теперь сам владеет cross-process/Supabase training lease, поэтому несколько инстансов не могут обучать execution model одновременно.
9. Время жизни потерянного distributed lease уменьшено с 900 до 360 секунд по умолчанию; heartbeat остаётся активным во время нормальной задачи.
10. Если distributed lease занят другим хостом, execution job освобождает локальную очередь и повторяется позже вместо монополизации локального heavy slot внутренними retry.
11. Добавлен progress-файл execution worker (`lease-wait/backfill/train/diagnose/done/error`).
12. Parent-процесс теперь раз в минуту пишет PID/stage/elapsed/RSS дочернего ML worker в обычный Docker log.
13. Перед новым auto cycle удаляются старые result/progress JSON, поэтому результат предыдущего запуска не может быть ошибочно принят за текущий.
14. Legacy signal symbol вида `GHO`, `CP`, `DEBIT`, `RMAIN`, `RH` теперь канонизируется в USDT perpetual формат (`GHOUSDT` и т.п.) до запроса бирж.
15. Исправлено определение symbol-bound API calls: раньше negative cache включался только если исходная строка уже заканчивалась `USDT`.
16. Ошибки бирж `invalid/unknown/unsupported symbol` переводятся в per-provider negative cache вместо повторных запросов на каждом fallback cycle.
17. Если все настроенные providers подтвердили отсутствие инструмента, включается global negative cache (по умолчанию 6 часов).
18. Outcome Tracker за один цикл прекращает повторные запросы для уже подтверждённо недоступного symbol и не размножает ошибку на 1h/4h/24h/72h.
19. `market-unavailable` больше не считается обычной processing error; возвращается отдельным `unsupported_symbols` summary.
20. Outcome Tracker использует сохранённый `execution_provider`/`exchange` как первый provider для исторического восстановления, затем fallback-цепочку.
21. Старые строки SQLite с base-only symbol автоматически нормализуются при обработке.
22. Удалена дублирующая инициализация `picked = {}` в dynamic universe builder.
23. Сохранён VPS MAX профиль v58.6.2: 4 CPU, Docker memory 6 GiB, ML workers 4, max execution rows 30k, 600 trees, HGB 800, regime 500, bootstrap 1200.

## Поведение после деплоя

Ручной backfill/train/diagnose не требуется. Execution pipeline автоматически проходит:

`queue -> distributed lease -> backfill -> train -> diagnose -> cloud publish/verify -> release lease`

Сетевые background задачи продолжают работать параллельно и не блокируют ML очередь.
