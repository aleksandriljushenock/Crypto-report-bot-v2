# v58.5.0 - Adaptive Evidence / Purged Nested Walk-Forward

## Исправленные проблемы
1. Фиксированный inner selection 15% заменен на адаптивную лестницу 15/20/25/30/35/40% с минимумом 60 строк.
2. Fallback при недостатке evidence расширяет только историческое окно и не использует test/future rows.
3. `inner_utility_unavailable` заменен на явный `NO_EVIDENCE` с detail и inner_attempts.
4. `BAD_REGIME` отделен от недостатка данных; подтвержденный veto не обходится старым prior.
5. Добавлен exit-aware purge между history и test.
6. Добавлен WF embargo, по умолчанию 1 час.
7. Устранен leakage от outcomes, закрывшихся после начала test.
8. Single-class alpha slice теперь fail-closed и не валит обучение.
9. Добавлены history_rows, purged_rows, inner_source, selection/calibration sizes.
10. Добавлены `walk_forward_no_evidence_folds` и `walk_forward_veto_folds`.
11. Для каждой adaptive попытки сохраняются fit/cal/selection rows, trades, PF и expectancy.
12. Schema обновлена до 585, VERSION до 58.5.0.

Profitability, drawdown, bootstrap CI, classifier, regime и OOD gates не ослаблялись.

## Новые параметры
`EXECUTION_WF_INNER_MIN_ROWS=60`
`EXECUTION_WF_CAL_MIN_ROWS=30`
`EXECUTION_WF_EMBARGO_HOURS=1`

## Проверка
pytest: 291 passed
compileall: PASS
Backfill не требуется.
