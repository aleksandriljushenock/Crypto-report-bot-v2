# AI Hedge Fund v1 + Supabase Sync

Перед деплоем выполните `SUPABASE_AI_HEDGE_FUND_V1.sql` в Supabase SQL Editor.

После миграции новые сигналы сохраняют в `learning_observations` отдельные поля:

- `quality_score`
- `calibrated_probability`
- `expected_value_pct`
- `quality_decision`
- `hedge_profile_version`

Полная диагностическая информация также дублируется в `metadata`, включая сработавшие профили и анти-профили. Это обеспечивает совместимость со старыми строками и позволяет восстанавливать обучение после перезапуска Render.

Для просмотра используйте:

```sql
select *
from public.learning_signal_quality_dashboard
order by signal_created_at desc
limit 100;
```

Проверка синхронизации:

```sql
select
  count(*) as total,
  count(*) filter (where quality_score is not null) as with_quality,
  count(*) filter (where expected_value_pct is not null) as with_ev,
  count(*) filter (where real_result is not null) as resolved
from public.learning_observations;
```
