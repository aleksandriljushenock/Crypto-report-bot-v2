# V39 — Execution Integrity & Security

V39 исправляет все проблемы, найденные в повторном аудите V38.

## Обязательная миграция
Перед запуском V39 выполнить в Supabase SQL Editor:

`migrations/SUPABASE_PAPER_V39_ATOMIC_LIFECYCLE.sql`

Миграция безопасна для повторного запуска.

## Основные исправления
- SECURITY DEFINER Paper RPC закрыты для PUBLIC/anon/authenticated; EXECUTE оставлен только service_role.
- Paper close теперь атомарен: close position + paper_trades + account aggregates выполняются одной PostgreSQL-транзакцией.
- Reconciliation и reset Paper переведены в атомарные PostgreSQL RPC.
- Старые `legacy_migrated_v38` сделки больше не считаются подтвержденной execution-историей автоматически.
- Paper boundary candles обрабатываются без использования OHLC-extremes вне legal event-time window.
- Для hold-window > ~16 часов Paper автоматически использует 5m history, чтобы 1000 свечей покрывали 72h окно после restart/downtime.
- Current-price execution предпочитает last/trade price, а mark price оставлен fallback, чтобы тип цены совпадал с OHLC.
- Strategy Lab forward tracker начинает outcome-анализ от `entered_at`, а boundary/same-bar свечи не используют pre-entry extrema.
- Active Strategy Lab setups больше не ограничены 30 строками; используется paged tracking до configured safety cap.
- Strategy notifications фильтруются по реально включенным стратегиям до формирования batch; breakeven закрытия поддерживаются.
- Strategy statistics читаются постранично без старого скрытого лимита 20k.
- Paper ledger repair проходит всю историю и ограничивает число repair-операций, а не глубину истории.
- Exchange pacing учитывает относительный weight endpoint-ов и `EXCHANGE_EXPECTED_INSTANCE_COUNT`.
- Circuit breaker стал method-scoped для обычных API методов, чтобы сбой одного endpoint не отключал всю биржу.
- Telegram webhook secret обязателен (минимум 24 символа); webhook без секрета не запускается.
- SQLite connections во всех Python-модулях переведены на ManagedConnection с гарантированным close.
- Runtime logs, pytest cache и pyc не включаются в production ZIP.

## Новые/важные env
- `TELEGRAM_WEBHOOK_SECRET` — обязательно, минимум 24 символа.
- `EXCHANGE_EXPECTED_INSTANCE_COUNT=1` — выставить фактическое число одновременно работающих экземпляров бота, если их больше одного.

## Проверка
- `python -m compileall -q .` — OK
- `python -m pytest -q` — 112 passed
- `python -m pytest -q -W error` — 112 passed
