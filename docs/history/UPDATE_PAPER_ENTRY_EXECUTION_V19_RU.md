# Paper Entry Execution v19

Исправлена ключевая ошибка Paper Trading: сигнал больше не считается исполненной сделкой сразу после публикации.

## Новая логика

- `PULLBACK`: создаётся `pending_entry`; бот ждёт реального касания рассчитанного `entryPrice` (середина отображаемой entry-zone). Пока касания нет, баланс, маржа и комиссии не меняются.
- `BREAKOUT`: бот ждёт пересечения trigger. Если в момент появления сигнала рынок уже ушёл от trigger дальше `PAPER_MAX_ENTRY_DEVIATION_PCT`, вход считается `MISSED_BREAKOUT`, а не исполняется задним числом.
- Pending-entry имеет срок жизни `PAPER_ENTRY_MAX_WAIT_HOURS`. Если цена не дошла — `ENTRY_EXPIRED` и сделка не входит в PnL/Win Rate.
- После fill плечо, ликвидация, quantity, комиссии и PnL считаются уже от фактического paper fill.
- Для проверки касания используются 5m candles, поэтому короткое касание между циклами монитора не теряется.

## TUTUSDT 2026-08-08 08:51 MSK

SQL `SUPABASE_PAPER_ENTRY_EXECUTION_V19.sql` автоматически находит позицию TUTUSDT в узком окне 05:45–06:05 UTC, отменяет `INVALID_FILL_PRE_V19` и возвращает её влияние на balance/equity/fees/realized PnL. Если позиция уже успела закрыться, связанная запись `paper_trades` удаляется и PnL откатывается.

## Railway ENV

Рекомендуемые значения:

```env
PAPER_ENTRY_MAX_WAIT_HOURS=12
PAPER_MAX_ENTRY_DEVIATION_PCT=0.50
PAPER_ENTRY_SLIPPAGE_PCT=0.03
```

`PAPER_ENTRY_SLIPPAGE_PCT` применяется к breakout stop-market исполнению. Pullback моделируется как limit touch по рассчитанной цене входа.
