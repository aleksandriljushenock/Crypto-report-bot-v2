# V37 — Paper Ledger Recovery + High-Capacity Analysis

## Что исправлено

- `paper_positions` теперь является каноническим источником результата paper-сделки.
- Если позиция успела перейти в `closed`, а запись в `paper_trades` не выполнилась, история и статистика всё равно видят сделку через fallback по закрытой позиции.
- Добавлен автоматический backfill пропущенных строк `paper_trades`.
- Добавлена периодическая сверка `paper_accounts` с фактической историей закрытых/открытых позиций.
- Ошибка записи ledger/account после закрытия больше не скрывает событие закрытия и Telegram-уведомление.
- Исправлен порядок фильтрации истории: валидные сделки не теряются из-за раннего `LIMIT`.
- Breakeven больше не считается проигрышем в Win Rate и loss streak.
- В performance-center отдельно показываются wins / losses / breakeven.

## Увеличенные лимиты для VPS

Финальный high-capacity профиль:

- `FAST_SCAN_POOL_SIZE=500`
- `TRADE_TOP_LIQUID_SYMBOLS=150`
- `TRADE_SCAN_BATCH_SIZE=16`
- `TRADE_SCAN_MAX_WORKERS=4`
- `HEDGE_CANDIDATE_POOL=40`
- dynamic universe: 84 liquid + 22 gainers + 22 losers + 22 coverage
- `NEAR_SIGNAL_RESCAN_LIMIT=40`
- `STRATEGY_LAB_MAX_SYMBOLS=200`
- `STRATEGY_LAB_PARALLEL_MAX_SYMBOLS=120`
- `STRATEGY_LAB_PARALLEL_THROTTLE_MS=150`

Render Free profile в `render.yaml` намеренно оставлен low-memory.

## Paper self-healing

- `PAPER_LEDGER_REPAIR_ENABLED=true`
- `PAPER_LEDGER_REPAIR_LIMIT=2000`
- `PAPER_RECONCILE_INTERVAL_MINUTES=5`

Новых SQL-миграций для V37 не требуется, если ранее применена миграция V24 с unique index `paper_trades(position_id)`.
