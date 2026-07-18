# Исправление торгового сканера для Render

На Render Binance Futures может отвечать HTTP 418. Эта версия переводит основной
торговый сканер и Trade Monitor на Bybit Linear Perpetual, сохраняя прежний формат
данных для анализатора.

Добавьте в Render → Environment:

```env
TRADE_MARKET_PROVIDER=bybit
BYBIT_API_BASE=https://api.bybit.com
EXCHANGE_HTTP_TIMEOUT=15
```

После изменения переменных выполните Manual Deploy → Deploy latest commit.

Ожидаемый результат: в логе больше не должно быть ошибки торгового монитора на
`fapi.binance.com/fapi/v1/exchangeInfo`. Ошибки Binance в отдельных старых
Smart Money источниках не влияют на запуск торгового сканера.
