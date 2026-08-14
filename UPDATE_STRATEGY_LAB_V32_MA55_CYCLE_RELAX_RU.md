# Strategy Lab v32 — MA55 Cycle v2

MA55 Cycle переработана после production-аудита отсутствия BUY-сигналов.

## Критический баг v30/v31

`analyze_ma55_cycle()` предварительно нормализовал H4 candles через `_closed()`, после чего `ma55_cycle_event()` повторно передавал эти dict-candles в `normalize_klines()`. `normalize_klines()` принимает raw list/tuple klines и silently отбрасывал dict rows. В результате BUY cross внутри analyzer мог не обнаруживаться вообще. Тот же дефект мог мешать reverse-cross EXIT в forward tracker.

В v32 MA55 helpers принимают и raw exchange rows, и уже нормализованные candle dicts. Добавлен regression test.

## Новая логика BUY

- Universe: 24h quote volume >= $100M.
- BUY cross: SMA55 завершает переход сверху вниз через EMA8/SMA13/SMA21. Сам переход может формироваться до 12 H4 bars.
- После завершения cross не забывается сразу: окно подтверждения 3 H4 bars (~12h).
- Это НЕ задержка: если условия готовы на cross candle, READY появляется сразу.
- D1 DOWN — hard block. D1 RANGE теперь разрешён.
- Для READY нужен clean stack: price > EMA8 > SMA13 > SMA21 > SMA55.
- Цена должна быть не дальше 2 ATR от EMA8.
- Нужны минимум 2 из 4 подтверждений:
  1. D1 UP или improving RANGE;
  2. SMA55 slope >= 0;
  3. RSI 48–76;
  4. volume >= 0.90 * avg20.
- Volume >= 1.20 * avg20 и H4 structure confirmation дают bonus к score, но не являются hard gate.
- SMA55 slope до -0.10% допускается в WATCH.
- Fingerprint привязан к исходной cross candle, поэтому повторные scans в течение 12h не создают дубли.

## Выход

Exit-правило не ослаблялось: штатный CLOSE LONG — когда SMA55 проходит EMA8/SMA13/SMA21 снизу вверх. Protective SL остаётся аварийной защитой.

## Диагностика

В summary добавлен `funnel` для MA55 Cycle. Telegram scan report показывает, где теряются монеты: D1 DOWN, wait clean stack, confirmation wait, overextended, cross expired, waiting cross.
