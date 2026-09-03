# v58.4.0 Regime Meta-Labeling / Bad-Regime Veto

Основание: диагностика v58.3 показала сильный локальный BREAKOUT edge, но нестабильный walk-forward: 118 OOS сделок, PF 1.10998, expectancy +0.0797%, CI95 нижняя граница -0.2709%, max drawdown 40.26%. Regime/OOD фильтры в проблемных folds часто не сокращали поток сделок.

## Доработки
1. Regime meta-label теперь означает прибыль выше минимального экономического edge (`EXECUTION_REGIME_MIN_PROFIT_PCT`), а не просто return > 0.
2. Для BREAKOUT economic path regime threshold больше не может fail-open в 0 при доступной regime-модели. Добавлен `EXECUTION_REGIME_MIN_PROBABILITY`.
3. OOD threshold стал частью nested selection: выбирается только на прошлом из безопасных квантилей и никогда не превышает train-only OOD threshold.
4. Добавлен fail-closed inner profitability veto. Если внутренний исторический selection имеет PF ниже `EXECUTION_WF_INNER_MIN_PF` или expectancy <= `EXECUTION_WF_INNER_MIN_EXPECTANCY`, будущий fold не торгуется (`inner_profitability_veto`). Это прямо закрывает сценарий v58.3, где отрицательный inner-selection всё равно разрешал OOS сделки.
5. Walk-forward champion дополнительно требует aggregate PF >= `EXECUTION_WF_MIN_AGGREGATE_PF`.
6. Добавлен hard gate aggregate max drawdown <= `EXECUTION_WF_MAX_AGGREGATE_DRAWDOWN`.
7. Сохранены строгие CI, minimum trades, positive-fold и classifier gates. Ничего не ослаблено.
8. GLOBAL/PULLBACK остаются без economic promotion; BREAKOUT specialist veto нельзя обойти GLOBAL fallback.
9. Версия bundle schema 584 / `execution-ensemble-v58.4-*`.

## Новые настройки по умолчанию
- EXECUTION_REGIME_MIN_PROFIT_PCT=0.10
- EXECUTION_REGIME_MIN_PROBABILITY=0.45
- EXECUTION_WF_INNER_MIN_PF=1.05
- EXECUTION_WF_INNER_MIN_EXPECTANCY=0.0
- EXECUTION_WF_MIN_AGGREGATE_PF=1.15
- EXECUTION_WF_MAX_AGGREGATE_DRAWDOWN=25.0

Цель v58.4 — уменьшить число плохих BREAKOUT входов и просадку, а не искусственно увеличить число champion-моделей.
