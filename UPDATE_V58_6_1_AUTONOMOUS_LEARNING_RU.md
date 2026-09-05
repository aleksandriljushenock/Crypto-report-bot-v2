# v58.6.1 — Autonomous Learning & Cloud Publish Fix

## Исправлено
- robust walk-forward для BREAKOUT запускается независимо от готовности main selection utility; main holdout и WF больше не блокируют друг друга;
- cloud publication использует компактный runtime-only bundle и joblib compression;
- после upload выполняется read-after-write SHA-256 + schema/version verification;
- training diagnostics больше не обязаны попадать в runtime cloud artifact;
- автоматический цикл execution ML сам делает backfill -> train -> diagnose;
- первый автоматический цикл после старта сокращен до 5 минут, далее используется EXECUTION_ML_TRAIN_INTERVAL_MINUTES;
- диагностический JSON автоматически сохраняется в data/execution_v58_6_1_latest_diagnostic.json;
- Telegram получает краткий итог обучения, если EXECUTION_ML_AUTO_NOTIFY=true;
- при ошибках backfill обучение fail-closed и не стартует;
- schema=5861, version prefix=execution-ensemble-v58.6.1.

## Эксплуатация
После git pull и пересборки контейнера ручные backfill/train/diagnose не требуются. Сервис выполняет цикл самостоятельно.
