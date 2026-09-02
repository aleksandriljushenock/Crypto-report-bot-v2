# Deploy v57.2 на VPS

1. Загрузить v57.2 в GitHub.
2. На VPS: `cd /opt/crypto-report-bot && git pull && cat VERSION` — должно быть `57.2.0`.
3. Пересобрать контейнер:
   `docker compose -f docker-compose.vps.yml down`
   `docker compose -f docker-compose.vps.yml build --no-cache`
   `docker compose -f docker-compose.vps.yml up -d`
4. Backfill повторять не нужно: используется существующая `execution_training_dataset_v57`.
5. Обучить Execution ML:
   `docker compose -f docker-compose.vps.yml exec -T crypto-bot python3 -c "from execution_model_v57 import train; import pprint; pprint.pp(train(trigger='v57.2-diagnostics'))"`
6. Получить компактную диагностику без повторного обучения:
   `docker compose -f docker-compose.vps.yml exec -T crypto-bot python3 diagnose_execution_v57.py > execution_v57_2_diagnostic.json`
