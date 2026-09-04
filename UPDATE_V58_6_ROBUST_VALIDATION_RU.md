# v58.6.0 — Robust Validation & Execution Economics

## Исправлено
- Inner threshold tuning отделён от untouched evidence holdout: одна выборка больше не используется и для подбора порогов, и для доказательства profitability.
- Alpha probability threshold больше не может быть 0; production floor задаётся `EXECUTION_ALPHA_MIN_PROBABILITY`.
- Adaptive evidence требует согласия нескольких исторических окон и не ищет старый «хороший» режим после надёжного recent BAD_REGIME.
- Добавлены threshold-drift gates.
- IID bootstrap заменён moving-block bootstrap; добавлен PF confidence interval.
- Добавлены stress-cost gate, CVaR/tail diagnostics и catastrophic micro-fold veto независимо от minimum valid fold size.
- Причины abstain разделены на NO_EVIDENCE, BAD_REGIME, small-sample, zero-trade и CATASTROPHIC.
- OOD threshold переопределён как maximum admissible score с явными `EXECUTION_OOD_MAX_SCORE`/`EXECUTION_OOD_TRAIN_FLOOR`.
- WF сохраняет provenance выбранных test indices, inner attempts и untouched evidence statistics.
- Paper PnL теперь учитывает funding по времени удержания; slippage зависит от base slippage, spread, volatility и notional impact при наличии этих decision-time данных.
- Release builder работает fail-closed: исключает runtime state/caches/logs/env/архивы и блокирует secret-like content.
- Удалены случайные файлы `int`, `SGDClassifier`, `None`, `0.5).astype(int)`, `.pytest_cache`, logs и ошибочный env-файл.
- Bundle schema 586, version prefix `execution-ensemble-v58.6-*`.

## Безопасность
Секреты из старых архивов в новую сборку не включаются. Если секрет из предыдущей сборки был реальным, его необходимо ротировать на стороне провайдера: удаление из нового ZIP не отзывает старый ключ.

## Политика promotion
BREAKOUT остаётся fail-closed. GLOBAL/PULLBACK не получают обход specialist veto. Champion требует nested WF profitability, block-bootstrap CI, PF CI, stress profitability, drawdown и отсутствие catastrophic folds.
