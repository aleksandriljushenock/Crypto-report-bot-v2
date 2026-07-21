# Обновление качества AI Self Learning v14

## Что изменено

1. Минимум для первого полноценного обучения увеличен до 200 наблюдений.
2. Похожие сигналы по одной монете, таймфрейму и направлению дедуплицируются в 20-минутном окне.
3. Горизонты результата приведены к единой схеме: 1h, 4h, 24h, 72h.
4. Накопление данных больше не создаёт ложные `completed` записи в `training_runs`.
5. Реальный запуск обучения сохраняет `samples_train`, `samples_validation`, список признаков и метрики.
6. Оценка модели дополнена `profit_factor`, `max_drawdown_pct_points`, `high_conf_precision`.
7. Champion/Challenger promotion требует минимум 40 holdout-наблюдений и проходит дополнительные проверки прибыли, просадки и калибровки.
8. Статус `collecting-data` сразу преобразуется в `running`, поэтому Supabase не получает заведомо недопустимый статус.
9. Сохранение прогресса сбора выполняется только на контрольных точках, чтобы не засорять Supabase.

## Перед деплоем

Выполнить в Supabase SQL Editor:

`SUPABASE_LEARNING_QUALITY_UPGRADE.sql`

## Переменные Render

```env
LEARNING_MIN_SAMPLES=200
LEARNING_SPECIALIST_MIN_SAMPLES=80
LEARNING_MIN_HOLDOUT_SAMPLES=40
LEARNING_DEDUPE_WINDOW_MINUTES=20
LEARNING_SIGNAL_DEDUPE_MINUTES=20
LEARNING_COLLECTION_SAVE_STEP=25
LEARNING_WALK_FORWARD_FOLDS=4
LEARNING_SEARCH_ITERATIONS=240
```

Для Render с ограниченной памятью можно оставить `LEARNING_SEARCH_ITERATIONS=100`.

## Что считать нормой после деплоя

До 200 завершённых наблюдений интерфейс показывает `collecting-data`. Это не сброс модели.
В `training_runs` новая строка появляется только после настоящего обучения, а не при каждом обновлении счётчика.
После обучения должны быть заполнены:

- `samples_train`;
- `samples_validation`;
- `metrics`;
- `feature_names`;
- `completed_at`.

## Проверка

```sql
select status, model_version, samples_total, samples_train,
       samples_validation, metrics, completed_at, created_at
from public.training_runs
order by created_at desc
limit 10;
```

```sql
select model_name, model_version, is_active, metrics, metadata, created_at
from public.model_registry
order by created_at desc
limit 10;
```
