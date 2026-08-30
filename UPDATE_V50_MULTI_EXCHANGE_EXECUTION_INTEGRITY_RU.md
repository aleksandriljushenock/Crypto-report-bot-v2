# V50.0.0 — Multi-Exchange & Execution Integrity

V50 закрывает баги, найденные при полном аудите V49.

## Основные исправления

- Добавлен единый `candle_contract.py` для Binance-shaped OHLCV:
  - `[0]` — open time;
  - `[6]` — реальный логический конец свечи, а не копия open time.
- Нормализованы Bybit, Bitget, Gate, HTX, KuCoin, MEXC Futures, OKX, BingX и Hyperliquid.
- `FallbackTradeMarketClient` повторно валидирует/нормализует свечи на общей границе абстракции.
- Добавлены корректные 1m mappings:
  - Bybit `1`;
  - HTX `1min`;
  - KuCoin `60`;
  - MEXC Futures `Min1`;
  - остальные провайдеры используют свой native `1m`.
- KuCoin больше не превращает неизвестный/1m interval в 1h fallback.
- MEXC Spot после серии HTTP 429 выдаёт нормальный `RuntimeError`, а не `UnboundLocalError`.

## Paper Trading

- Введён строгий chronological barrier.
- Если более ранняя boundary-свеча могла содержать Entry/SL/TP/Liquidation, никакая более поздняя свеча не может определить fill или PnL.
- Boundary, которая физически не могла содержать торговое событие, не блокирует дальнейшую историю.
- Одновременный TP+SL на полностью наблюдаемой свече остаётся консервативным `SL_CONSERVATIVE`.

## Shadow

- Если boundary candle могла содержать entry, поиск не продолжается на более поздних свечах до разрешения более ранней неопределённости.
- Сохранена interval-aware execution precision.

## Тестовая инфраструктура

- 23 старых root `test_*.py` перенесены в `scripts/manual_checks/` и переименованы в `manual_*.py`.
- Убраны pytest import collisions и сетевые side effects во время test collection.
- Добавлен `pytest.ini` с production unit/regression suite в `tests/`.
- Добавлены behavioral/contract tests для multi-exchange candles, native 1m intervals, MEXC 429 и Paper chronological ordering.

## Release hygiene

Финальный релиз не содержит `.git`, runtime `data`, `cache`, logs, nested ZIP, SQLite/DB, `.coverage`, `.pytest_cache`, `__pycache__` или `.pyc`.
