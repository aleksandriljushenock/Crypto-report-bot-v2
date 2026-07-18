# Health Monitor + адаптивное обучение

## Что добавлено

- Отдельный Health Monitor проверяет Trade Monitor и все PeriodicWorker.
- Упавший или зависший Trade Monitor автоматически перезапускается.
- Упавшие фоновые сервисы автоматически запускаются заново.
- `/health` теперь показывает состояние Trade Monitor, автосервисов и Health Monitor.
- Адаптивный обучающий слой загружает завершённые наблюдения из Supabase.
- Веса факторов изменяются постепенно и ограничены безопасным шагом ±12%.
- При недостатке данных или недоступности Supabase используется текущая validated-модель.

## Переменные Render

```env
HEALTH_MONITOR_ENABLED=true
HEALTH_MONITOR_INTERVAL_SECONDS=120
TRADE_MONITOR_STALE_SECONDS=1500
HEALTH_RESTART_COOLDOWN_SECONDS=120
ADAPTIVE_CLOUD_MIN_SAMPLES=20
ADAPTIVE_CLOUD_MAX_ROWS=1000
ADAPTIVE_WEIGHT_MAX_STEP=0.12
ADAPTIVE_WEIGHT_LEARNING_RATE=0.35
```

Изменение весов выполняется во время `self-learning-engine`. До накопления минимального числа завершённых сделок статус будет `collecting-data`.
