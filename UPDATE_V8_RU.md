# Crypto Report Service v8 Professional

## Добавлено

- Capital Flow Engine: funding, open interest, taker buy/sell, volume acceleration, CVD proxy.
- Smart Money Scanner с подключаемыми RSS/API feeds.
- Narrative AI по новостным потокам.
- Fear & Greed Engine.
- Portfolio Manager с командами `/portfolio`, `/portfolio_add`, `/portfolio_del`.
- AI News Engine с дедупликацией и Impact Score.
- Self Learning Engine, который хранит адаптивные веса по накопленной статистике.
- Web Dashboard на Flask.
- Единая SQLite база `data/v8_professional.db`.

## Команды Telegram

`/pro`, `/flows`, `/smartmoney`, `/narratives`, `/sentiment`, `/news`, `/portfolio`, `/learn`.

Добавление позиции:

```text
/portfolio_add BTC 0.1 60000
```

Удаление:

```text
/portfolio_del BTC
```

## Dashboard

```cmd
pip install -r requirements.txt
run_dashboard.bat
```

Открыть: `http://127.0.0.1:8080`

## Важное ограничение

Arkham, Nansen, ETF-flow, точные liquidation heatmaps и некоторые whale feeds не имеют универсального бесплатного публичного API. В v8 подготовлена рабочая архитектура подключаемых источников через `SMART_MONEY_FEEDS` и `NEWS_RSS_FEEDS`. Без собственных ключей эти блоки честно показывают статус «источники не настроены», а не выдумывают данные.

`CVD` и liquidation heatmap в бесплатном режиме являются прокси-метриками из taker ratio, объёма, волатильности и OI.
