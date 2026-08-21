# V38 — Paper Execution Safety & Consistency

V38 исправляет баги, найденные при полном ревью V37.

## Обязательно перед запуском

В Supabase SQL Editor выполните:

`migrations/SUPABASE_PAPER_V38_ATOMIC_EXECUTION.sql`

Миграция безопасна для повторного запуска. Она:
- заменяет partial unique index `paper_trades(position_id)` на обычный UNIQUE index для корректного `ON CONFLICT(position_id)`;
- добавляет `paper_positions.execution_provider`;
- восстанавливает `fill_price_source` для валидных legacy closed-позиций;
- добавляет атомарные RPC `paper_create_pending_v38` и `paper_fill_pending_v38`.

## Исправлено

### Critical
1. Исправлен конфликт Supabase/PostgreSQL при upsert `paper_trades` по `position_id`.
2. Paper больше не использует OHLC экстремумы свечи, начавшейся до фактического fill/signal boundary.
3. Pending entry не может исполниться после `pending_until`; проверка есть и в Python, и атомарно в PostgreSQL RPC.
4. Исторические свечи обрабатываются раньше текущего ticker — более поздний TP больше не перекрывает более ранний SL/liquidation.
5. `max_hold_until` является жёсткой границей. Свечи после deadline не участвуют, TIME_EXIT получает цену последней допустимой свечи и timestamp deadline.

### High
6. Резервирование margin + entry fee выполняется атомарно с переводом pending -> open под row lock в PostgreSQL.
7. Ограничение `PAPER_MAX_OPEN_POSITIONS` и one-position-per-symbol проверяется атомарно при создании pending order.
8. При недоступности Paper account система работает fail-closed и не создаёт synthetic $100 account.
9. Неизвестные направления (`HOLD`, пустое значение, опечатка) отклоняются вместо автоматического LONG.
10. Circuit breaker больше не обходится, когда все providers находятся на cooldown.
11. Добавлен process-wide per-provider rate pacing (`EXCHANGE_PROVIDER_MAX_RPS`, default 8 RPS), общий для scanner/Strategy Lab/background workers.
12. AI Optimizer больше не может предложить уменьшить universe 150 -> 100 под видом расширения. Новый ceiling: `AI_OPTIMIZER_UNIVERSE_MAX` (default 300).
13. Render cron tasks теперь проходят через тот же heavy-task guard, memory guard и global lock, что фоновые задачи.

### Medium / statistics / UI
14. Общая Paper статистика по умолчанию lifetime: `PAPER_STATS_MAX_TRADES=0`. История читается страницами по 1000 строк.
15. Reconciliation больше не ограничен последними 5000 закрытиями.
16. Legacy legitimate closed positions без `fill_price_source` возвращены в статистику; `INVALID_FILL*` по-прежнему исключаются.
17. AI Optimizer считает breakeven отдельно и исключает его из знаменателя Win Rate.
18. Paper position закрепляется за `execution_provider`, чтобы execution OHLC и current price не смешивались между биржами. Legacy open position закрепляется за первым успешно использованным provider.
19. `derived_equity` теперь включает mark-to-market unrealized PnL открытых позиций; отдельно сохранён `realized_equity`.
20. Strategy Lab больше не обрезает durable scan result до первых 50 записей; сохраняется весь результат выбранного universe.

## Новые настройки

```env
EXCHANGE_PROVIDER_MAX_RPS="8"
PAPER_STATS_MAX_TRADES="0" # 0 = lifetime/unlimited
AI_OPTIMIZER_UNIVERSE_MAX="300"
```

## Проверка

- `python -m compileall -q .` — OK
- `pytest -q` — 112 passed, 0 failed
- Добавлены V38 regression tests для temporal boundaries, invalid direction, breakeven, universe > 100 и circuit breaker.
