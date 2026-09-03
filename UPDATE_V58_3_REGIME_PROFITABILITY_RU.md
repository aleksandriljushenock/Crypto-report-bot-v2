# v58.3.0 — Regime-Aware Profitability Engine

Цель версии: не увеличивать количество сделок, а повысить вероятность отбора прибыльных BREAKOUT|LONG сделок и fail-closed пропускать плохие/неизвестные режимы.

## Что изменено

- Добавлена отдельная regime-quality модель TRADE/SKIP, обучаемая только на decision-time признаках рынка.
- Добавлен robust OOD detector по regime-вектору. Сигналы вне обученного распределения могут быть автоматически отброшены.
- Joint selector теперь использует четыре условия: expected return, P(profit), regime probability и OOD threshold.
- Walk-forward полностью переделан в nested anchored validation: каждый fold заново обучает classifier, return model и regime model только на прошлом; thresholds настраиваются только на inner-selection внутри прошлого.
- По умолчанию 5 walk-forward folds вместо 3.
- Fold с недостаточным числом сделок считается abstain, а не доказательством прибыльности.
- Economic champion требует минимум 3 прибыльных valid folds, минимум 60 совокупных OOS сделок и положительную нижнюю 95% bootstrap-границу expectancy.
- В profitability objective добавлены downside deviation и max drawdown penalty.
- Диагностика fold расширена: даты test-периода, funnel после return/alpha/regime/OOD фильтров, thresholds, PF, expectancy и drawdown.
- Live prediction применяет regime/OOD veto к economic champion.
- Исправлен критичный fail-open сценарий: specialist veto больше нельзя обойти fallback на GLOBAL.
- GLOBAL и PULLBACK не получают economic champion path; production gate по-прежнему ориентирован на BREAKOUT|LONG.

## Новые настройки

- EXECUTION_WF_FOLDS=5
- EXECUTION_WF_TEST_FRACTION=0.08
- EXECUTION_WF_MIN_POSITIVE_FOLDS=3
- EXECUTION_WF_MIN_TOTAL_TRADES=60
- EXECUTION_WF_MIN_EXPECTANCY_CI_LOW=0
- EXECUTION_BOOTSTRAP_REPS=400
- EXECUTION_REGIME_MAX_ITER=250
- EXECUTION_REGIME_LEARNING_RATE=0.04
- EXECUTION_REGIME_MAX_LEAVES=15
- EXECUTION_REGIME_L2=2.0
- EXECUTION_OOD_TRAIN_QUANTILE=0.98
- EXECUTION_OOD_MIN_THRESHOLD=1.5

## Важно

v58.3 не ослабляет существующие AUC/Brier/champion safety gates. Новые economic gates только добавляют требования к устойчивости прибыли. Реальные сделки не должны включаться вручную до получения walk_forward_ok=true и champion_ok=true на новой истории.
