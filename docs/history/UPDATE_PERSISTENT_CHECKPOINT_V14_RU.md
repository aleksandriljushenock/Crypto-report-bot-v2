# Сохранение процесса AI Self Learning MAX v14

Обновление сделано поверх предыдущей версии с Chronos-Bolt и Learning Quality v14.
Существующие функции, Chronos, Supabase observations, Champion/Challenger и текущие 42 результата не удаляются.

## Что теперь сохраняется

1. `learning_observations` и реальные результаты — как раньше в Supabase.
2. Активная модель и challenger-модели — как раньше в `model_registry`.
3. Локальная база `learning_v14.db` целиком:
   - model_versions;
   - calibration_bins;
   - learning_rules;
   - learning_runs;
   - drift_snapshots.

Каждые 10 минут создаётся согласованная SQLite-копия и загружается в приватный Supabase Storage bucket `learning-checkpoints`.
После рестарта Render база автоматически восстанавливается до инициализации Learning Engine.

## Установка

1. Выполнить `SUPABASE_LEARNING_CHECKPOINTS.sql` в Supabase SQL Editor.
2. Заменить проект файлами из архива.
3. Добавить переменные из `.env.example` в Render.
4. Выполнить Clear build cache & deploy.

## Проверка логов

После первого сохранения:

`Learning checkpoint saved: reason=periodic bytes=...`

После рестарта Render:

`Learning checkpoint restored: ... bytes -> data/learning_v14.db`

Если bucket не создан или ключ неверный, бот продолжит работать, но в логах будет `Learning checkpoint save failed`.

## Важно по текущим 42 прогнозам

Они не зависят только от локального checkpoint. Learning Engine восстанавливает завершённые наблюдения из `learning_observations`, поэтому накопленный набор не должен обнулиться после деплоя. Checkpoint дополнительно сохраняет локальные правила, калибровку, drift и версии моделей.
