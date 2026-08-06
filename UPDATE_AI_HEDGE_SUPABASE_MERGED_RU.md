# Итоговая объединённая сборка

Сборка выполнена поверх архива `Crypto-report-bot(1).zip`.

Сохранены существующие модули:

- Supabase cloud-first learning synchronization;
- checkpoint и восстановление обучения;
- Learning MAX v14 / MAX 2.0;
- Chronos;
- Render memory guard и keepalive;
- текущий Telegram-интерфейс.

Добавлены:

- AI Hedge Fund EV/Quality Gate;
- расширенный пул кандидатов перед финальным отбором;
- сортировка по Expected Value;
- профиль `data/profit_profile_v2.json`;
- отдельные поля Hedge Fund в Supabase;
- представление `learning_signal_quality_dashboard`;
- резервная запись Hedge-метрик в `metadata`, если SQL-миграция ещё не применена.

## Перед деплоем

1. Выполнить `SUPABASE_AI_HEDGE_FUND_V1.sql` в Supabase SQL Editor.
2. Добавить настройки HEDGE из `.env.example` в Render.
3. Выполнить Deploy latest commit.

## Проверка

```sql
select
  count(*) as total,
  count(*) filter (where quality_score is not null) as with_quality,
  count(*) filter (where expected_value_pct is not null) as with_ev,
  count(*) filter (where real_result is not null) as resolved
from public.learning_observations;
```

```sql
select *
from public.learning_signal_quality_dashboard
order by signal_created_at desc
limit 50;
```
