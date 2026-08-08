# Render: Binance fallback и самовосстановление Trade Monitor

## Что изменено

- Рыночные запросы выполняются в порядке `Binance -> Bybit`.
- При HTTP 418/429/5xx, timeout или другой ошибке Binance автоматически открывается circuit breaker.
- Заблокированный источник пропускается 15 минут, затем проверяется снова.
- Торговый скан, Capital Flow, Portfolio и Outcome Tracker используют единый fallback-клиент.
- Trade Monitor автоматически восстанавливается после перезапуска Render.
- Любая критическая ошибка фонового потока приводит к автоматическому перезапуску через 10 секунд.
- В лог добавлены heartbeat, время следующего цикла и счётчик перезапусков.

## Переменные Render

```env
TRADE_MARKET_PROVIDERS=binance,bybit
EXCHANGE_PROVIDER_COOLDOWN_SECONDS=900
BYBIT_API_BASE=https://api.bybit.com
EXCHANGE_HTTP_TIMEOUT=15
MONITOR_AUTO_ENABLE=true
```

Удалите старую переменную `TRADE_MARKET_PROVIDER=bybit`, иначе она принудительно оставит только один источник.

## Ожидаемые логи

При блокировке Binance:

```text
Trade market provider failed: method=exchange_info provider=binance ... 418
Trade market fallback: method=exchange_info provider=bybit
```

Монитор:

```text
Фоновый торговый монитор запущен.
Монитор: проверено=..., сигналов=..., новых=...
Монитор: следующий цикл через ... сек.
```
