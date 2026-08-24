# V40.2 — Paper Trading direction hotfix

Исправлен критический regression: основной торговый сканер создаёт направления `LONG_BIAS` / `SHORT_BIAS`, а Paper Trading после ужесточения в V38 принимал только `LONG` / `SHORT` / `BUY` / `SELL`. Из-за этого Telegram-сигнал отправлялся, но Paper возвращал `invalid-direction` и не создавал ни `pending_entry`, ни открытую позицию.

Теперь Paper нормализует `LONG_BIAS -> LONG` и `SHORT_BIAS -> SHORT`, при этом неизвестные направления по-прежнему fail-closed. В автоматический TradeMonitor добавлено явное логирование причины, если Paper не смог зарегистрировать полученный финальный сигнал.
