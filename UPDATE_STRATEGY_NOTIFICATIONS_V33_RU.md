# v33 — Strategy Lab Notifications

Исправлена доставка Telegram-уведомлений по всем стратегиям Strategy Lab.

## Найденные проблемы

1. Детальные уведомления `STRATEGY SIGNAL` были реализованы только для `ma55_cycle`.
2. В параллельном режиме Strategy Lab запускается из `TradeMonitor`, поэтому настройка сводного `STRATEGY_LAB_AUTO_NOTIFY_READY` не гарантировала детальное уведомление.
3. Наличие setup в `strategy_setups` ошибочно использовалось как косвенный признак того, что пользователь уже был уведомлён. Если setup сначала появился при ручном скане или предыдущем проходе, будущий автоскан уже не создавал `new_ready_event`.
4. При временной ошибке Telegram уведомление могло быть потеряно навсегда: состояние setup уже было сохранено, а отдельного состояния доставки не существовало.

## Новая схема

У каждого setup теперь независимо хранятся:

- `ready_notified_at` — отправлена идея входа;
- `open_notified_at` — отправлено фактическое исполнение;
- `close_notified_at` — отправлено закрытие.

Флаг ставится только после успешного `sendMessage`. При ошибке Telegram сообщение повторится в следующем цикле.

Уведомления:

- `READY` → `🧭 STRATEGY SIGNAL` с Entry / SL / TP / R/R / Score;
- `OPEN` → `✅ STRATEGY ENTRY FILLED`;
- `won/lost` → `🏁/🛑 STRATEGY CLOSED` с результатом.

Это работает для всех Strategy Lab стратегий, а не только MA55.

## Миграция

Один раз выполнить:

`migrations/SUPABASE_STRATEGY_NOTIFICATIONS_V33.sql`

## ENV

Рекомендованные defaults уже включены:

```env
STRATEGY_LAB_NOTIFY_ENABLED=true
STRATEGY_LAB_NOTIFY_READY=true
STRATEGY_LAB_NOTIFY_FILLED=true
STRATEGY_LAB_NOTIFY_CLOSED=true
STRATEGY_LAB_NOTIFY_MAX_AGE_HOURS=24
STRATEGY_LAB_NOTIFY_MAX_PER_CYCLE=30
```

`STRATEGY_MA55CYCLE_NOTIFY` сохранён для обратной совместимости и управляет только MA55 Cycle.
