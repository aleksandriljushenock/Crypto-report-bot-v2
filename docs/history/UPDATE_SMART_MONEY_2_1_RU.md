# Smart Money Engine 2.1

## Причина прежнего покрытия 0%

Старая реализация читала только `SMART_MONEY_FEEDS` непосредственно из окружения и фактически рассчитывала один RSS-компонент `whale_alert`. Funding, Open Interest, ETF Flow, Stablecoin Flow, Exchange Netflow и Liquidations не имели сборщиков данных.

## Что изменено

- Добавлен `smart_money_sources.py` с независимыми адаптерами источников.
- Семь компонентов собираются параллельно и изолированы по ошибкам.
- Добавлены Binance Funding и Open Interest.
- Добавлен DefiLlama Stablecoin Supply Flow.
- Добавлен Farside Bitcoin ETF Flow.
- Добавлены бесплатные proxy для whale activity, exchange netflow и liquidations на основе крупных/агрессивных сделок Binance.
- При наличии `COINGLASS_API_KEY` liquidation proxy автоматически заменяется прямым источником.
- Отчёт показывает общее покрытие, прямое покрытие, уверенность, источник, качество и ошибку каждого компонента.
- Снимок полностью сохраняется в SQLite через существующий `v8_store`.
- Сохранена совместимость функций `calculate_smart_money_score`, `scan_smart_money`, `build_smart_money_report`.

## Важно о качестве данных

`Exchange Netflow`, `Whale Activity` и `Liquidations` без специализированного платного on-chain/derivatives API являются рыночными proxy. Они явно помечены словом `proxy` и не выдаются за размеченные движения кошельков или точные суммы ликвидаций.

## Проверка

```bash
python test_smart_money.py
python healthcheck.py
```

В среде без доступа к интернету тесты используют mock-ответы. На сервере источники проверяются независимо: отказ одного источника не останавливает отчёт.
