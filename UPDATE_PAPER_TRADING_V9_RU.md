# Paper Trading v9

## Что реализовано

- стартовый paper-баланс 100 USDT;
- автоматическое открытие виртуальной позиции по каждому финальному сигналу;
- размер маржи берётся из `suggestedPositionSizeUsd` (обычно $3/$4/$5);
- автоматический подбор плеча по расстоянию до стопа;
- оценочная ликвидация располагается за стопом с настраиваемым запасом;
- isolated-style paper accounting;
- комиссии на вход/выход и проскальзывание;
- закрытие по TP1, SL или тайм-ауту 72 часа;
- если TP и SL попали в одну 5-минутную свечу, применяется консервативный SL;
- состояние и история хранятся в Supabase;
- Telegram: статистика, позиции, история, ON/OFF и безопасный reset.

## Перед деплоем

Запустить в Supabase SQL Editor:

`SUPABASE_PAPER_TRADING_V9.sql`

## Render ENV

```env
PAPER_TRADING_ENABLED=true
PAPER_TRACKER_ENABLED=true
PAPER_INITIAL_BALANCE_USD=100
PAPER_UPDATE_INTERVAL_MINUTES=5
PAPER_MAX_OPEN_POSITIONS=10
PAPER_ONE_POSITION_PER_SYMBOL=true
PAPER_MAX_LEVERAGE=20
PAPER_LIQUIDATION_BUFFER_PCT=0.5
PAPER_MAINTENANCE_MARGIN_PCT=0.5
PAPER_FEE_PCT_PER_SIDE=0.06
PAPER_SLIPPAGE_PCT=0.03
PAPER_MAX_HOLD_HOURS=72
PAPER_MIN_FREE_BALANCE_USD=5
```

## Telegram

`📈 Торговый центр → 🧪 Paper Trading`

или команда `/paper`.

## Важно

Цена ликвидации является консервативной оценкой, а не точной формулой конкретной биржи. Перед live trading расчёт будет заменён на exchange-specific формулу с учётом maintenance margin tier.
