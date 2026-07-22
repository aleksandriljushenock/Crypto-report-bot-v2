# AI Self Learning MAX v14 — Cloud-first синхронизация

Эта версия создана поверх предыдущей сборки с Chronos-Bolt, Learning Quality v14 и persistent checkpoints.

## Что было причиной сброса

`learning_observations` содержала 35 строк, но все строки имели:

- `training_status = pending`;
- `real_result = null`;
- `resolved_at = null`.

При этом результаты исходов находились только в локальном `data/trade_outcomes.db`. Файловая система Render временная, поэтому после деплоя локальная база исчезла, а Learning Engine увидел 0 завершённых прогнозов.

## Новая архитектура

Supabase теперь является главным источником истины:

1. Сигнал сначала сохраняется в `learning_observations`.
2. Только после подтверждённой записи он кешируется локально.
3. Outcome Tracker при каждом цикле загружает все `pending`-строки из Supabase.
4. Потерянная локальная `trade_outcomes.db` автоматически пересоздаётся.
5. Для просроченных сигналов загружаются 5-минутные исторические свечи и восстанавливаются результаты 1h/4h/24h/72h.
6. Каждый рассчитанный результат немедленно записывается обратно в ту же строку Supabase.
7. Learning Engine строит метрики из `real_result` в Supabase, поэтому рестарт Render больше не обнуляет обучение.

Локальные SQLite-файлы являются только кешем и дополнительным checkpoint.

## Что сохраняется в real_result

```json
{
  "returns": {"1h": 0.7, "4h": 1.2},
  "prices": {"1h": 64200.0, "4h": 64500.0},
  "labels": {"1h": "OPEN", "4h": "TP1"},
  "latest_horizon": "4h",
  "return_percent": 1.2,
  "target": 1,
  "success": true,
  "updated_at": "..."
}
```

После появления первого горизонта строка получает:

- `training_status = ready`;
- `resolved_at`;
- `market_price_after`;
- `price_change_pct`;
- `outcome`;
- `outcome_score`.

Следующие горизонты дописываются в тот же `real_result`, а не создают дубликат.

## Установка

### 1. Выполнить SQL

В Supabase SQL Editor выполнить:

```text
SUPABASE_LEARNING_SYNC_V14.sql
```

SQL:

- стандартизирует статусы `pending/processing/ready/failed`;
- заполнит пропущенные `signal_created_at` и `resolve_after`;
- создаст индексы;
- удалит дубликаты по `metadata.fingerprint`.

### 2. Заменить файлы проекта

Распаковать архив поверх предыдущей версии.

### 3. Отправить изменения

```powershell
cd C:\Users\user\GitHub\Crypto-report-bot
git add .
git commit -m "Fix durable cloud-first learning synchronization"
git push
```

### 4. Render

Добавить переменные:

```env
LEARNING_READY_HORIZON=1h
LEARNING_CLOUD_MAX_ROWS=3000
LEARNING_OUTCOMES_CHECKPOINT_PATH=v14/latest/trade_outcomes.db
TRADE_OUTCOMES_DB_PATH=data/trade_outcomes.db
```

Затем выполнить обычный Manual Deploy. Clear build cache не обязателен, если зависимости не менялись.

## Что произойдёт с текущими 35 pending-записями

На первом цикле `trade-outcome-tracker` они будут импортированы из Supabase. Для уже наступивших горизонтов бот попытается найти цену по историческим 5-минутным свечам. После успешного расчёта строки станут `ready`, а интерфейс Learning MAX снова начнёт показывать завершённые прогнозы и метрики.

Предыдущие 42 локальных результата полностью восстановить невозможно, поскольку в Supabase были сохранены только 35 исходных сигналов без результатов. Но эти 35 сигналов новая версия доразметит максимально точно по доступной истории рынка.

## Проверка

В логах ожидается:

```text
Trade outcomes: imported=35, updated=..., cloud_synced=35, errors=0
```

SQL-проверка:

```sql
select
    count(*) as total,
    count(*) filter (where real_result is not null) as with_real_result,
    count(*) filter (where resolved_at is not null) as resolved,
    count(*) filter (where training_status = 'ready') as ready_for_training,
    max(updated_at) as latest_update
from public.learning_observations;
```

Распределение статусов:

```sql
select training_status, count(*)
from public.learning_observations
group by training_status
order by count(*) desc;
```

Проверка последних результатов:

```sql
select
    symbol,
    timeframe,
    signal_direction,
    signal_created_at,
    resolved_at,
    training_status,
    real_result
from public.learning_observations
order by updated_at desc
limit 20;
```
