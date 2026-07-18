# Discovery Engine v6

## Что изменено

HTML-парсинг страниц объявлений заменён на публичные market/instruments API бирж.
Система больше не зависит от Cloudflare-страниц Gate, MEXC, Bitget, HTX,
CoinEx и LBank.

Подключены источники:

- Binance Spot
- Bybit Spot
- OKX Spot
- KuCoin Spot
- Gate Spot
- Bitget Spot
- MEXC Spot
- HTX Spot
- BingX Spot
- CoinEx Spot
- LBank Spot
- CoinGecko и CoinMarketCap остаются в Early Discovery

## Как определяется новый листинг

Для каждой биржи сохраняется снимок доступных торговых пар. На следующем цикле
новыми считаются пары, которых не было в предыдущем успешном снимке.

Если API отдаёт время открытия торгов, на первом запуске также импортируются пары,
открытые за последние 45 дней. Источник, завершившийся ошибкой, не перезаписывает
старый снимок.

## Настройки .env

```env
DISCOVERY_RECENT_DAYS=45
DISCOVERY_CONNECT_TIMEOUT=7
DISCOVERY_READ_TIMEOUT=30
DISCOVERY_HTTP_RETRIES=3
```

## Проверка

```cmd
python -m py_compile exchange_announcement_sources.py
python -c "from exchange_announcement_sources import collect_exchange_sources; import json; print(json.dumps(collect_exchange_sources(), ensure_ascii=False, indent=2)[:15000])"
```

На первом запуске у источников может появиться `initialized: true` и `count: 0`.
Это нормально: создан исходный снимок. При добавлении новой пары следующий цикл
вернёт её как новый листинг.
