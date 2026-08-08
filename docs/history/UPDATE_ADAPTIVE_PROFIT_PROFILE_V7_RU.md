# Adaptive Profit Profile v7

## Что реализовано

- веса пяти основных profit-profile правил вынесены в Render ENV;
- основные пороги Score / Probability / R/R / Quality / EV читаются из ENV;
- добавлен безопасный интерфейс recency-профиля (`recent_groups`);
- если свежая статистика отсутствует, бот продолжает использовать долгосрочный профиль без ошибки;
- добавлен динамический рекомендуемый размер позиции $3 / $4 / $5;
- размер позиции показывается в Telegram, но бот по-прежнему не открывает сделки автоматически.

## Рекомендуемые ENV

```env
TRADE_MIN_SCORE=72
TRADE_MIN_PROBABILITY=70
TRADE_MIN_RR=2.3
QUALITY_MIN_RR=2.3
HEDGE_MIN_QUALITY=70
HEDGE_MIN_EV_PCT=2.0

PROFILE_RECENCY_ENABLED=true
PROFILE_HALF_LIFE_DAYS=14
PROFILE_RECENT_WINDOW_DAYS=21
PROFILE_MIN_RECENT_SAMPLES=30
PROFILE_RECENT_WEIGHT=2.0

RULE_WEIGHT_PROBABILITY_TREND_LIQUIDITY=10
RULE_WEIGHT_DAILY_MICRO_ALIGNMENT=8
RULE_WEIGHT_FLOW_ALIGNMENT_VOLUME=5
RULE_WEIGHT_SMART_PULLBACK_PROBABILITY=3
RULE_WEIGHT_SMART_PULLBACK_VOLUME=2

POSITION_SIZING_ENABLED=true
POSITION_SIZE_BASE_USD=3
POSITION_SIZE_STRONG_USD=4
POSITION_SIZE_MAX_USD=5
```

## Важное ограничение recency

Текущий `data/profit_profile_v2.json` содержит долгосрочные агрегаты. Новая версия уже умеет читать `recent_groups`, но они начнут влиять на вероятность только после пересборки профиля по свежей выгрузке Supabase. До этого `PROFILE_RECENCY_*` безопасно не меняют результат.
