# AI Hedge Fund v1 — единая доработка качества сигналов

Обновление построено поверх текущего проекта с Learning MAX v14, Chronos,
cloud-first Supabase sync и checkpoint-системой.

## Что включено

- Expected Value (EV) на основе калиброванной вероятности и реальной геометрии TP/SL.
- Исторические профили setup/regime/structure/timeframe/symbol на 1144 сигналах.
- Profit-профили и анти-профили.
- Лёгкий ensemble: Learning MAX + исторический контекст + Chronos (когда включён).
- Байесовское сглаживание маленьких групп, чтобы одна монета не создавала ложные 90%.
- Отдельные правила направления; LONG против 4H DOWN блокируется.
- Quality Gate: в Telegram проходят только сигналы с положительным EV и Quality выше порога.
- Расширенный пул кандидатов до фильтрации, чтобы строгий фильтр не обрезал хорошие сигналы.
- В Telegram выводятся Quality, EV, calibrated probability, profit/anti profiles.

## Render ENV

```env
HEDGE_QUALITY_GATE_ENABLED=true
HEDGE_MIN_QUALITY=70
HEDGE_MIN_EV_PCT=0.20
HEDGE_MAX_RULE_ADJUSTMENT=24
HEDGE_CANDIDATE_POOL=20
HEDGE_DEFAULT_RISK_PCT=1.0
PROFIT_PROFILE_PATH=data/profit_profile_v2.json
```

Рекомендуемый старт: Quality 70, EV 0.20%. Через 100–200 новых resolved
наблюдений пороги нужно перепроверить. Более строгий режим: Quality 75 и EV 0.35%.

## Ограничения

Исторический профиль основан почти полностью на LONG-сигналах. Для SHORT
движок применяет общую EV-логику, но не переносит LONG-специфичные бонусы.
MFE/MAE, время сессии, BTC dominance и корреляционный риск не включены в
жёсткий gate, потому что в исходной выгрузке эти данные отсутствуют или не
заполнены. Их нельзя достоверно оптимизировать без новой истории.
