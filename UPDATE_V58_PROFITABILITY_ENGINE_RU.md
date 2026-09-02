# v58.0.0 — Profitability Engine

Цель: не ослаблять safety/champion gates, а улучшить экономическую OOS-валидацию return-модели.

- Robust return target: winsor 5/95 + HistGradientBoostingRegressor с absolute_error.
- MAE сравнивается на robust scale; реальные PF/expectancy считаются по исходному net_return_pct после costs.
- Utility threshold подбирается только на selection-сегменте; champion/OOS не используется для подбора.
- Для champion обязательны положительная selection expectancy, selection PF >= 1.05, достаточное число OOS trades, положительная OOS expectancy и прежний OOS PF >= 1.10.
- Старые AUC/Brier/Precision@20/Spearman/sign gates не ослаблены.
- PULLBACK/GLOBAL не продвигаются автоматически: статус по-прежнему fail-closed.
