# V54 Execution Intelligence

Версия 54 переводит качество сигналов от proxy-оптимизации к реальным first-hit execution labels.

## Ключевые изменения

- Новый Supabase dataset `execution_training_dataset_v54`.
- Backfill `backfill_execution_dataset_v54.py` восстанавливает реальный путь сделки по 5m Binance Futures candles с явными start/end timestamps.
- NO_FILL и PROFIT разделены: обучаются отдельные P(fill) и P(profit | fill).
- Outcome replay совпадает с текущим Paper execution: закрытие на TP1/SL/TIME_EXIT; если SL и TP1 попали в одну OHLC свечу, используется консервативный SL.
- Неопределённые исторические окна не превращаются в отрицательные labels.
- Rolling HistGradientBoosting ensembles обучаются на окнах 250/500/1000 samples.
- Строгий chronological train/calibration/test split; isotonic calibration не оценивается на данных, на которых она обучалась.
- Promotion модели только при OOS AUC/Brier лучше сохранённой signal Probability baseline.
- Отдельные specialists PULLBACK/BREAKOUT × LONG/SHORT с fallback на validated GLOBAL model.
- Отдельный Expected Return regressor.
- V54 ML влияет на live только когда model bundle имеет status=champion.
- ML bundle сохраняется локально и best-effort в Supabase Storage.
- Execution evidence shrink'ится только к execution prior, а не к mark-to-market prior.
- Generic setup guard блокирует любой setup с отрицательной execution expectancy, а не только BREAKOUT.
- Severe degradation работает по OR-условиям PF / Brier / Win Rate и переводит live в SHADOW_ONLY.
- V54 first-hit shadow outcomes подключаются к Learning MAX как `shadow_execution_v54`.
- SHORT scarcity больше не замораживает LONG champion: направления обучаются как независимые specialists.
- Reliability сильнее штрафует нейтральные/fallback narrative, smart_money, capital_flow и OI.

## Обязательная миграция Supabase

Перед первым backfill выполнить содержимое:

`migrations/SUPABASE_EXECUTION_INTELLIGENCE_V54.sql`

## Первый backfill

После миграции:

```bash
python backfill_execution_dataset_v54.py --limit 10000
```

Backfill ничего не угадывает: если Binance historical candles не позволяют доказать outcome, строка остаётся `UNRESOLVED`.

## Обучение V54 ML

После backfill:

```bash
python -c "from execution_model_v54 import train; print(train(trigger='manual'))"
```

Если OOS качество не проходит floors, будет `status=shadow`, и модель не повлияет на live signals.

## Рекомендуемые параметры мощного сервера

Использовать `.env.v54.high_capacity.example` как справочник, не как замену текущего `.env` целиком.
