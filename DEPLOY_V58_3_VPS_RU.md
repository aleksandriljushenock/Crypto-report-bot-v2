# Deploy v58.3.0 на VPS

После push в GitHub:

```bash
cd /opt/crypto-report-bot
git pull
cat VERSION
```

Ожидается `58.3.0`.

Пересборка:

```bash
docker compose -f docker-compose.vps.yml down
docker compose -f docker-compose.vps.yml build --no-cache
docker compose -f docker-compose.vps.yml up -d
```

Backfill повторять не требуется.

Обучение:

```bash
docker compose -f docker-compose.vps.yml exec -T crypto-bot python3 -c "from execution_model_v57 import train; import pprint; pprint.pp(train(trigger='v58.3-regime-profitability'))"
```

Диагностика:

```bash
docker compose -f docker-compose.vps.yml exec -T crypto-bot python3 diagnose_execution_v57.py > execution_v58_3_diagnostic.json
```

Проверить:

```bash
ls -lh execution_v58_3_diagnostic.json
```
