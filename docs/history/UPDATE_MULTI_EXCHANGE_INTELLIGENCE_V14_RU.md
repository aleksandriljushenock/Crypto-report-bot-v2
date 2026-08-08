# Multi Exchange Intelligence v14

## Что изменилось

Торговый universe теперь может собираться одновременно с пяти публичных USDT perpetual рынков:

- Binance Futures
- Bybit Linear Futures
- OKX USDT SWAP
- Bitget USDT Futures
- Gate.io USDT Futures

Список монет объединяется по каноническому символу (например BTCUSDT), дубликаты удаляются. Для ранжирования используется максимальный 24h quote volume среди бирж и небольшой бонус за присутствие актива на нескольких площадках.

После выбора монеты тяжёлые данные (свечи, стакан, OI, funding, long/short) по-прежнему загружаются через fallback-цепочку. Клиент запоминает, на каких биржах символ существует, и не делает заведомо бесполезные запросы.

Отсутствие символа на одной бирже больше не ставит всю биржу в cooldown. Circuit breaker включается только при сетевых/лимитных/серверных ошибках.

## Railway / VPS ENV

Обязательно обновить:

```env
TRADE_MARKET_PROVIDERS=binance,bybit,okx,bitget,gate
MULTI_EXCHANGE_UNIVERSE_ENABLED=true
MULTI_EXCHANGE_MIN_VENUES=1
MULTI_EXCHANGE_MIN_QUOTE_VOLUME_USDT=50000000
MULTI_EXCHANGE_COVERAGE_BONUS=0.08
MULTI_EXCHANGE_UNIVERSE_TIMEOUT=8
```

Для Railway Trial (1 GB RAM) начать с:

```env
TRADE_TOP_LIQUID_SYMBOLS=30
TRADE_SCAN_MAX_WORKERS=1
HEDGE_CANDIDATE_POOL=8
```

После 2-3 стабильных сканов можно попробовать `TRADE_TOP_LIQUID_SYMBOLS=36`, затем 40, контролируя RAM.

## Telegram

`📊 Рынок -> 🏦 Биржи` показывает пять настроенных площадок. После первого multi-exchange скана рядом с биржей дополнительно отображается число доступных USDT perpetual контрактов и число контрактов, прошедших минимальную ликвидность.

В карточке торгового сигнала показывается, на каких биржах присутствует актив.

## Supabase

Новой SQL-миграции нет. Новые runtime settings автоматически добавятся в `strategy_settings` существующим seed-механизмом.

## Важное

Добавление бирж расширяет universe, но не отменяет Quality / Probability / EV / R/R фильтры. Поэтому количество сигналов может вырасти только за счёт новых подходящих монет, а не за счёт ослабления качества.
