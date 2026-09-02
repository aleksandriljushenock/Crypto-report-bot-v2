# v57.2 — Execution ML diagnostics

Версия 57.2 не ослабляет profitability/Champion gates и не меняет торговые пороги.

Что исправлено:
- каждая обученная Execution-модель сохраняет явные `gate_failures` для runtime и champion validation;
- отдельно фиксируются причины провала AUC, Brier, baseline/base-rate comparisons, Precision@20 и return/PF gates;
- `train()` возвращает `gate_failure_counts` и компактный `group_summary` с лучшими кандидатами;
- добавлен `diagnose_execution_v57.py`, который читает уже сохранённый joblib и печатает компактный JSON без повторного обучения;
- ошибка cloud upload больше не скрывается полностью: `train()` возвращает `cloud_error`, если `cloud_saved=false`;
- сохранены adaptive purged split и все fail-closed ограничения v57/v57.1.

После установки повторный backfill не нужен. Запустить обучение и затем диагностику:

```bash
docker compose -f docker-compose.vps.yml exec -T crypto-bot python3 -c "from execution_model_v57 import train; import pprint; pprint.pp(train(trigger='v57.2-diagnostics'))"
docker compose -f docker-compose.vps.yml exec -T crypto-bot python3 diagnose_execution_v57.py > execution_v57_2_diagnostic.json
```
