# Supabase migrations — final refactor

Для уже работающей базы, где предыдущие миграции v9–v22 уже применялись, достаточно один раз выполнить:

`SUPABASE_FINAL_REFACTOR_V24.sql`

Он повторно фиксирует lifecycle Paper Trading, добавляет индекс только для реально исполненных закрытых сделок и пересоздаёт `learning_signal_quality_dashboard` как `security_invoker`, одновременно устраняя старую проблему `cannot change name of view column`.

Для чистой базы применяй исторические SQL по возрастанию версии, затем `SUPABASE_FINAL_REFACTOR_V24.sql` последним.

## Strategy Lab v25/v26

После `SUPABASE_FINAL_REFACTOR_V24.sql` один раз выполни `SUPABASE_STRATEGY_LAB_V25.sql`, если таблицы `strategy_scan_runs` и `strategy_setups` ещё не создавались.

Для v26 отдельной SQL-миграции нет: все 11 стратегий используют ту же нормализованную схему Strategy Lab.

## V39
Для V39 обязательно выполнить `SUPABASE_PAPER_V39_ATOMIC_LIFECYCLE.sql` после миграции V38. Она закрывает SECURITY DEFINER RPC от public/anon/authenticated и добавляет атомарные close/reconcile/reset.

## V43
Для V43 обязательно один раз выполнить `SUPABASE_ADAPTIVE_MODEL_V43_ATOMIC_STORE.sql` после создания таблицы `adaptive_model_versions` из V18. Миграция добавляет атомарное сохранение/promotion Adaptive Champion и закрывает RPC от public/anon/authenticated.

## V45
После V43/V44 выполнить `SUPABASE_V45_INTEGRITY.sql`. Миграция удаляет fingerprint-дубли, включает idempotent create learning observations и compare-and-promote для Adaptive Champion между несколькими инстансами.

## V48
После V47 выполните `SUPABASE_V48_INTEGRITY.sql`.
Миграция исправляет namespace Cloud Champion, добавляет fenced lease generation, атомарный compare-and-promote V48 и terminal void/reconcile для Paper execution-data gaps.

## V49 Execution Integrity
После V48 выполнить `SUPABASE_V49_EXECUTION_INTEGRITY.sql`. Миграция повторяемая: безопасно нормализует legacy namespace и добавляет durable terminal-outcome RPC.
