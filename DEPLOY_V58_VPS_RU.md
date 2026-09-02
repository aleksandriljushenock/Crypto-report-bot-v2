# Deploy v58

```bash
cd /opt/crypto-report-bot
git pull
cat VERSION
docker compose -f docker-compose.vps.yml down
docker compose -f docker-compose.vps.yml build --no-cache
docker compose -f docker-compose.vps.yml up -d
```

После запуска обучить и получить диагностику:

```bash
docker compose -f docker-compose.vps.yml exec -T crypto-bot python3 -c "from execution_model_v57 import train; import pprint; pprint.pp(train(trigger='v58-profitability'))"
docker compose -f docker-compose.vps.yml exec -T crypto-bot python3 diagnose_execution_v57.py > execution_v58_diagnostic.json
```
