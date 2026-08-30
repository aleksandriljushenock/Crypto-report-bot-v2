# V52 — Learning Correctness Release

## Цель
Исправить деградацию обучения и несоответствие между ML-метриками и реальным Paper Trading.

## Ключевые изменения

1. **Execution-first target**
   - Закрытые и валидные Paper-позиции имеют приоритет над 24h mark-to-market.
   - Target строится по `net_pnl`, `return_on_margin`, `R-multiple` и `close_reason`.
   - 24h результат остаётся fallback только для неисполненных сигналов.
   - Execution samples получают повышенный вес в обучении.

2. **Защита от legacy-target моделей**
   - V52 требует `target_schema=execution_v52`.
   - Старые v14-модели, обученные на mark-to-market target, не используются как активный champion после деплоя V52.
   - До появления валидного V52 champion используется безопасная базовая конфигурация без legacy calibration/rules.

3. **Cloud champion как source of truth**
   - При Supabase runtime активная модель читается из cloud registry.
   - Promotion после RPC обязательно перепроверяется повторным чтением active champion.
   - Локальная ephemeral SQLite больше не должна silently override cloud champion.

4. **Dataset health watchdog**
   - Promotion блокируется при stale dataset.
   - Promotion блокируется при LONG/SHORT дисбалансе.
   - Promotion блокируется при недостаточном числе execution outcomes.
   - Контролируется symbol/day concentration.
   - Выявляются constant/near-constant features; они исключаются из оптимизации.

5. **SHORT parity**
   - Исправлена асимметрия directional score, которая раньше структурно награждала LONG и штрафовала bearish structure.
   - LONG/SHORT технические условия теперь рассчитываются зеркально.

6. **Final execution gate**
   - Probability, Quality, EV и RR проверяются после Hedge + Adaptive + Chronos.
   - Нельзя открыть Paper/выдать торговый сигнал, если финальная probability упала ниже `TRADE_MIN_PROBABILITY`.

7. **Execution-calibrated probability / EV**
   - Добавлена Bayesian calibration по фактическим закрытым Paper outcomes.
   - Execution calibration используется как conservative prior, когда накоплено минимум достаточно сделок.
   - EV пересчитывается уже после execution calibration.

8. **Adaptive Model safety**
   - Adaptive champion не участвует в runtime blend, пока нет минимум 150 закрытых валидных Paper trades.
   - Кандидаты могут продолжать обучаться/оцениваться, но не влияют на production probability преждевременно.

9. **Chronos shadow mode**
   - По умолчанию Chronos продолжает прогнозировать, но не меняет probability.
   - Для включения blend требуется `CHRONOS_PROBABILITY_BLEND_ENABLED=true` после подтверждённого OOS uplift.

10. **Walk-forward hardening**
    - Минимум 4 folds независимо от устаревшего ENV=2.
    - Добавлен temporal embargo между train и validation.

11. **Correlated sample protection**
    - Сигналы одного symbol/day получают кластерный вес `1/sqrt(cluster_size)`.
    - Рекомендуемый dedupe window увеличен до 60 минут.

12. **Timeframe metadata**
    - Scanner сохраняет `timeframe=multi_tf` и `primaryTimeframe=15m` вместо `unknown`.

13. **Low-memory infrastructure fencing**
    - При `LOW_MEMORY_MODE=true` Supabase strategy settings больше не могут увеличить workers/top symbols/batch/hedge pool выше безопасных инфраструктурных cap.

## Новые рекомендуемые параметры

- `LEARNING_CLOUD_MAX_ROWS=5000`
- `LEARNING_WALK_FORWARD_FOLDS=4+`
- `LEARNING_WALK_FORWARD_EMBARGO_SAMPLES=5`
- `LEARNING_EXECUTION_TARGET_ENABLED=true`
- `LEARNING_EXECUTION_SAMPLE_WEIGHT=4.0`
- `LEARNING_MIN_EXECUTION_SAMPLES_FOR_PROMOTION=30`
- `LEARNING_MIN_DIRECTION_SAMPLES=40`
- `LEARNING_MAX_DATA_AGE_HOURS=72`
- `LEARNING_FEATURE_MIN_STD=0.75`
- `ADAPTIVE_MODEL_RUNTIME_MIN_TRADES=150`
- `CHRONOS_PROBABILITY_BLEND_ENABLED=false`
- `EXECUTION_CALIBRATION_MIN_TRADES=20`
- `EXECUTION_CALIBRATION_MAX_WEIGHT=0.25`

## Важное поведение после деплоя
V52 намеренно может стать заметно более консервативной сразу после запуска, потому что текущая Paper history имеет низкий фактический win rate. Это ожидаемая safety-мера: бот должен сначала перестать доверять завышенной legacy probability, накопить двустороннюю LONG/SHORT execution history и только потом продвигать V52 champion.
