# v57.1 — Adaptive Purged Split Fix

Исправлен P0-баг Execution Model v57: фиксированный 72-часовой embargo мог полностью очищать calibration/selection/champion сегменты на плотном потоке сигналов. В результате `_fit_one()` возвращал `None` до `clf.fit()`, а bundle сохранялся с пустыми `fill`/`outcome` массивами.

## Что изменено

- 55/15/15/15 chronological split сохранён.
- Embargo остаётся включённым, но теперь адаптивно уменьшается от 72 часов до безопасного минимального значения, пока каждый сегмент не сохраняет `EXECUTION_ML_MIN_SEGMENT_SAMPLES`.
- Unpurged fallback не используется при корректных timestamps.
- Добавлены `split_meta`, effective embargo и список попыток.
- Все ранние отказы обучения теперь сохраняются в `rejections` с конкретной причиной вместо молчаливого `None`.
- Итог `train()` содержит `trained_models` и `rejection_counts`.
- Окна расширены до `500,1000,2500,5000`, чтобы использовать накопленный execution dataset и не ограничиваться слишком короткими плотными окнами.
- Добавлены regression-тесты для плотного потока сигналов.

Quality/Profitability gates не ослаблялись. Исправление позволяет моделям фактически обучиться, но Champion по-прежнему будет разрешён только после прохождения OOS AUC/Brier/return/PF gate.
