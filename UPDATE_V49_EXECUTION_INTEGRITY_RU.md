# V49.0.0 — Execution Integrity

- Paper: ambiguous boundary candle больше не продвигает execution cursor; current ticker и TIME_EXIT не используются поверх unresolved boundary.
- Shadow: boundary candle, способная содержать Entry, переводит сигнал в entry_unresolved, а не expired.
- Shadow: filled_at теперь равен концу фактически использованной свечи; в cloud payload сохраняется execution_precision.
- V48 migration сделана безопасной для сосуществующих active legacy/canonical model namespaces; добавлена повторяемая V49 remediation migration.
- Terminal outcome markers теперь атомарно merge-ятся в Supabase через V49 RPC и восстанавливаются в local outcome_failures после redeploy.
- Версия: 49.0.0.
