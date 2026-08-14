# Strategy Lab v36 — durable statistics

## Что было до v36

Сырые READY-setups и их forward outcomes уже сохранялись в Supabase в `strategy_setups`, поэтому данные переживали redeploy. Но экран статистики каждый раз пересчитывался на лету и был ограничен последними 2000 строками. Отдельного долговечного агрегата и истории изменения метрик не было.

## Что изменено

- статистика считается по всей доступной истории `strategy_setups` с постраничным чтением;
- добавлен durable current aggregate `strategy_statistics`;
- добавлен дневной snapshot `strategy_stats_daily`;
- aggregate обновляется автоматически после outcome tracking, даже если пользователь не открывал экран статистики;
- добавлен fallback на последний сохранённый aggregate при временной ошибке чтения истории;
- Win Rate считает только wins/losses; breakeven вынесен отдельно;
- добавлен compounded return;
- Max Drawdown теперь считается по compounded equity curve, а не как просадка суммы процентов;
- additive cumulative return оставлен для совместимости старого UI/leaderboard.

## Миграция

Один раз выполнить `migrations/SUPABASE_STRATEGY_STATISTICS_V36.sql`.
