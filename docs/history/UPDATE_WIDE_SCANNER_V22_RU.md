# V22 — Wide Fast Scan + Near Signals + Shadow Outcomes

## Что изменено

1. **Wide Fast Scan**: публичные тикеры со всех настроенных бирж просматриваются дешёво, до 250 ликвидных инструментов остаются в fast-pool. Свечи, SMC и AI загружаются только для Deep Scan.
2. **Dynamic Universe**: Deep Scan больше не состоит только из топа по объёму. Он смешивает ликвидность, лидеров роста, лидеров падения и инструменты с высоким cross-exchange coverage.
3. **Near-Signal Watchlist**: почти прошедшие кандидаты пересканируются каждые несколько минут, не дожидаясь следующего полного цикла.
4. **Adaptive Scan Frequency**: при большом числе near-signals или активном рынке полный цикл автоматически ускоряется.
5. **Shadow Signals**: отклонённые близкие кандидаты не отправляются пользователю и не открывают Paper-позицию, но их реальный вход и исход отслеживаются отдельно.
6. **Три профиля оценки**: PULLBACK, BREAKOUT и MOMENTUM имеют отдельные пороги. MOMENTUM не меняет геометрию входа — это профиль оценки поверх исходного setup.
7. **Scanner UX**: статус показывает реального владельца scan-lock, этап и прогресс. Добавлены Near Signals и Shadow в Scanner Intelligence.

## Рекомендуемый старт для Railway

```env
FAST_SCAN_POOL_SIZE=250
TRADE_TOP_LIQUID_SYMBOLS=80
TRADE_SCAN_BATCH_SIZE=8
TRADE_SCAN_MAX_WORKERS=2
HEDGE_CANDIDATE_POOL=28
MULTI_EXCHANGE_MIN_QUOTE_VOLUME_USDT=15000000
NEAR_SIGNAL_WATCH_ENABLED=true
NEAR_SIGNAL_RESCAN_MINUTES=5
SHADOW_SIGNALS_ENABLED=true
SHADOW_CLOUD_ENABLED=true
```

Если RAM стабильно выше 800–850 MB, сначала уменьшить `TRADE_TOP_LIQUID_SYMBOLS` до 70, а не workers.

## Supabase

Перед деплоем один раз выполнить `SUPABASE_WIDE_SCANNER_V22.sql`.
