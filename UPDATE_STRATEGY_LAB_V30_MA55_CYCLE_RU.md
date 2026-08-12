# Strategy Lab v30 — MA55 Cycle

Добавлена независимая стратегия `MA55 Cycle 8/13/21/55`.

## Вход
- universe: USDT perpetual, 24h quote volume >= $100M;
- H4: EMA8, SMA13, SMA21, SMA55;
- BUY: SMA55 завершает переход сверху вниз через все три fast-линии (переход может завершаться в окне до 12 закрытых H4 свечей);
- фильтры качества: D1 UP, bullish stack, положительный slope SMA55, RSI 50–72, отсутствие сильного ATR-extension, плюс volume/structure votes.

## Исполнение / статистика
Сигнал появляется после закрытой H4 свечи. Для forward-статистики entry фиксируется по open первой будущей закрытой/доступной 1H свечи — цена задним числом не используется.

Обычный выход: SMA55 проходит EMA8/SMA13/SMA21 снизу вверх. Выход фиксируется по close H4 свечи, на которой переход завершён. Дополнительно есть защитный risk-stop под H4 structure/MA55 на случай резкого обвала до обратного пересечения.

В Strategy Lab считаются Win Rate, Profit Factor, expectancy, cumulative return и max drawdown.

## Telegram
При новом READY стратегия отправляет `MA55 CYCLE — BUY SIGNAL`; при reverse-cross — `MA55 CYCLE — CLOSE LONG`; при аварийном stop — `EMERGENCY EXIT`.

Переменные:
```env
STRATEGY_MA55CYCLE_MIN_VOLUME_USDT=100000000
STRATEGY_MA55CYCLE_NOTIFY=true
STRATEGY_MA55CYCLE_PRIORITY_ENABLED=true
```

Новая SQL миграция не нужна: используются таблицы Strategy Lab v25.

При `STRATEGY_MA55CYCLE_PRIORITY_ENABLED=true` эта стратегия проверяется на каждом основном цикле дополнительно к обычному round-robin, поэтому BUY/CLOSE не ждут полного круга по всем стратегиям.
