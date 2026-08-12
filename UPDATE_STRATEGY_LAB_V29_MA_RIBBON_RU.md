# Strategy Lab v29 — MA Ribbon 8/13/21/55

Добавлена стратегия `MA Ribbon 8/13/21/55` для ликвидных USDT perpetual.

## Важная корректировка направления

Буквальное условие «SMA55 пересекает EMA8/SMA13/SMA21 снизу вверх» обычно является медвежьим событием: медленная средняя оказывается выше быстрых. Поэтому оно детектируется и сохраняется в payload как `literal_55_cross_up`, но не создаёт BUY READY.

Для BUY используется рыночно согласованная версия: быстрый ribbon `EMA8 + SMA13 + SMA21` переходит выше `SMA55`.

## READY

- 24h quote volume >= $100M (через общий Strategy Lab universe);
- D1 trend = UP;
- свежий H4 переход fast ribbon выше SMA55;
- clean stack: `price > EMA8 > SMA13 > SMA21 > SMA55`;
- slope SMA55 > 0;
- цена не перерастянута более чем на 1.5 ATR от EMA8;
- дополнительно учитываются volume expansion, RSI 52–72 и H4 structure confirmation;
- требуется минимум 6 из 7 quality votes.

## Execution

Сигнал READY не получает мгновенный виртуальный fill. Entry использует STOP-confirmation выше high закрытой H4 сигнальной свечи. SL ставится ниже локальной H4 структуры / SMA55 с ATR buffer. TP — D1 high, если он даёт минимум 2R, иначе 2.5R.

## ENV

Обязательных новых переменных нет. Общий фильтр уже работает через:

`STRATEGY_LAB_MIN_VOLUME_USDT=100000000`

Опционально отдельный порог стратегии:

`STRATEGY_MARIBBON_MIN_VOLUME_USDT=100000000`

## Проверка

- полный pytest suite: 75 passed;
- compileall: PASS.
