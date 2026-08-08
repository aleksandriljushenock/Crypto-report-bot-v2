# V21 — Extended Multi-Exchange Coverage

Добавлены публичные perpetual/futures источники:

- MEXC Futures
- BingX Perpetual Futures
- KuCoin Futures
- Hyperliquid Perpetuals
- HTX USDT-M Swaps

Итоговая стандартная цепочка теперь:

`BINANCE → BYBIT → OKX → BITGET → GATE → MEXC → BINGX → KUCOIN → HYPERLIQUID → HTX`

Новые биржи участвуют одновременно в двух задачах:

1. расширяют общий universe монет;
2. служат fallback-источниками цены/свечей/стакана, если предыдущая площадка не знает символ или временно недоступна.

Для бирж, где публичный API не предоставляет совместимый long/short или taker buy/sell показатель, возвращается нейтральное значение. Оно не должно создавать ложное подтверждение Smart Money.

Hyperliquid использует perp-рынки и нормализуется к внутреннему виду `COINUSDT`, чтобы существующий движок мог сопоставлять активы между площадками. Это идентификатор внутри сканера; фактическая котировка Hyperliquid может отличаться по settlement/quote asset.

## Рекомендуемые Railway variables

```env
TRADE_MARKET_PROVIDERS=binance,bybit,okx,bitget,gate,mexc,bingx,kucoin,hyperliquid,htx
TRADE_TOP_LIQUID_SYMBOLS=70
TRADE_SCAN_BATCH_SIZE=8
TRADE_SCAN_MAX_WORKERS=2
HEDGE_CANDIDATE_POOL=20
MULTI_EXCHANGE_MIN_QUOTE_VOLUME_USDT=20000000
MULTI_EXCHANGE_MIN_VENUES=1
MULTI_EXCHANGE_COVERAGE_BONUS=0.08
```

На Railway Trial сначала проверь 2–3 полных скана и RAM. Если память стабильно ниже ~750 MB, `TRADE_TOP_LIQUID_SYMBOLS` можно поднять до 80. Не увеличивай workers выше 2 одновременно с ростом universe.

## Что не менялось

Quality, Probability, EV, R/R и прибыльные профили не ослаблялись. Рост числа сигналов должен идти за счёт большего охвата рынка, а не ухудшения порогов качества.
