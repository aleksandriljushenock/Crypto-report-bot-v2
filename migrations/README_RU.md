# Supabase migrations — final refactor

Для уже работающей базы, где предыдущие миграции v9–v22 уже применялись, достаточно один раз выполнить:

`SUPABASE_FINAL_REFACTOR_V24.sql`

Он повторно фиксирует lifecycle Paper Trading, добавляет индекс только для реально исполненных закрытых сделок и пересоздаёт `learning_signal_quality_dashboard` как `security_invoker`, одновременно устраняя старую проблему `cannot change name of view column`.

Для чистой базы применяй исторические SQL по возрастанию версии, затем `SUPABASE_FINAL_REFACTOR_V24.sql` последним.

## Strategy Lab v25/v26

После `SUPABASE_FINAL_REFACTOR_V24.sql` один раз выполни `SUPABASE_STRATEGY_LAB_V25.sql`, если таблицы `strategy_scan_runs` и `strategy_setups` ещё не создавались.

Для v26 отдельной SQL-миграции нет: все 11 стратегий используют ту же нормализованную схему Strategy Lab.
