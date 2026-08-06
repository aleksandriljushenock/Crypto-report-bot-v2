# Profit Signal Profile v1

Обновление добавляет второй этап отбора после AI Learning. Он не меняет сбор данных, Supabase, Chronos и checkpoint-механику.

## Основа профиля

Стартовые LONG-правила сформированы по 1 144 завершённым сигналам:

- PULLBACK получает преимущество перед BREAKOUT;
- сильнейшая широкая комбинация: `capital_flow >= 62`, `alignment >= 75`, `volume >= 65`;
- дополнительные комбинации: Smart Money + Pullback + Volume/Probability;
- жёсткие риски: низкая ликвидность, низкая уверенность, высокая неопределённость и конфликт LONG с 4H DOWN.

Правила для SHORT намеренно остаются нейтральными до накопления отдельной статистики SHORT.

## Новые поля сигнала

- `qualityScore`
- `qualityDecision`
- `qualityReasons`
- `qualityHardBlocks`
- `qualityProfile`

В Telegram показываются итоговый Quality Score и главные причины.

## Render ENV

```env
QUALITY_GATE_ENABLED=true
QUALITY_MIN_SCORE=72
QUALITY_MIN_QUOTE_VOLUME=130000000
QUALITY_MIN_RR=2.3
QUALITY_MAX_ADJUSTMENT=25
```

Для более частых сигналов снизить `QUALITY_MIN_SCORE` до 68. Для максимально строгого режима повысить до 76–80.

Важно: профиль повышает селективность на исторической выборке, но не гарантирует будущую прибыль. Сначала рекомендуется paper-trading и повторная проверка на новых данных.
