# Strategy Lab v26 — 11 independent strategies

Strategy Lab расширен с одной Fib 0.5 стратегии до 11 независимых торговых гипотез. Все стратегии используют одинаковый forward-tracking: READY setup сохраняется как `waiting_entry`, а сделка считается открытой только после будущего фактического касания/trigger. Исторические свечи до момента сигнала не используются для фиктивного fill.

## Добавленные стратегии

1. Fib 0.5 Pullback
2. Liquidity Sweep + Reclaim
3. EMA Trend Pullback
4. Breakout → Retest
5. Range Mean Reversion
6. Anchored VWAP Pullback
7. Volatility Squeeze
8. Donchian Trend Following
9. Funding + OI Squeeze
10. OI / Price Divergence
11. RSI Divergence + Structure

## Telegram

`🧭 Стратегии` теперь показывает все 11 стратегий и общий `🏆 Leaderboard`.

У каждой стратегии одинаковое простое меню:

- `🔍 Анализировать монеты`
- `📈 Статистика`
- `🟡 Кандидаты`
- `📜 История`
- `🔄 Outcomes`
- `📐 Правила стратегии`

Старые callbacks Fib v25 сохранены для уже отправленных Telegram-сообщений.

## Forward outcomes

Поддерживаются LONG и SHORT, а также два типа входа:

- `LIMIT` — pullback/reversion setup ждёт возврата цены к entry;
- `STOP` — breakout/confirmation setup ждёт будущего пробоя trigger.

Если в одной 1H свече после входа одновременно доступны TP и SL, результат считается `SL_AMBIGUOUS`. Это сознательно консервативная модель.

## Derivatives strategies

`Funding + OI Squeeze` и `OI / Price Divergence` используют capability-aware market client. Если funding/OI недоступны, стратегия возвращает NO_SETUP и не подставляет `0` вместо отсутствующих данных.

## Нагрузка

Strategy Lab не запускается параллельно с основным trading scanner. Одновременно выполняется только одна стратегия. Trade Monitor и Near Watch также пропускают цикл, пока активен любой Strategy Lab scan.

Рекомендуемые defaults:

```env
STRATEGY_LAB_MIN_VOLUME_USDT=100000000
STRATEGY_LAB_MAX_SYMBOLS=120
STRATEGY_LAB_D1_LIMIT=240
STRATEGY_LAB_H4_LIMIT=220
```

## Supabase

Новой схемы БД для v26 не требуется. Используются таблицы `strategy_scan_runs` и `strategy_setups`, созданные миграцией:

`migrations/SUPABASE_STRATEGY_LAB_V25.sql`

Если v25 migration ещё не применялась, выполнить её один раз перед использованием Strategy Lab.

## Статистика и Leaderboard

Leaderboard сортирует стратегии сначала по достаточности выборки, затем по Profit Factor и expectancy. До 30 завершённых setups стратегия маркируется как экспериментальная (`🧪`).

Высокий Win Rate сам по себе не считается достаточным доказательством edge.
