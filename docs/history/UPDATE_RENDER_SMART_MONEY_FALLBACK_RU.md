# Render: fallback для Smart Money источников

На общих IP Render Binance может отвечать HTTP 418. В этой версии Smart Money источники автоматически переключаются между биржами.

Порядок для Render по умолчанию:

1. Bybit
2. OKX
3. Binance

Поддерживается для:

- funding rate;
- open interest (Bybit/Binance);
- spot trade flow;
- whale activity proxy;
- liquidation stress proxy.

В Render рекомендуется добавить переменную:

```env
SMART_MONEY_EXCHANGES=bybit,okx,binance
```

Binance остаётся последним резервным источником. Если не хотите обращаться к Binance вообще:

```env
SMART_MONEY_EXCHANGES=bybit,okx
```

После изменения переменной выполните Manual Deploy → Deploy latest commit или дождитесь автоматического деплоя.
