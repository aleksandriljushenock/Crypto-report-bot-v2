# Deploy V57 на VPS

## 1. Supabase
В SQL Editor выполнить целиком:

`migrations/SUPABASE_EXECUTION_INTELLIGENCE_V57.sql`

Миграция создаёт `execution_training_dataset_v57`, чинит JSONB defaults Learning и ужесточает безопасные runtime-настройки.

## 2. Обновить код на VM

```bash
cd /opt/crypto-report-bot
git pull
cat VERSION
```

Ожидается `57.0.0`.

```bash
docker compose -f docker-compose.vps.yml down
docker compose -f docker-compose.vps.yml build --no-cache
docker compose -f docker-compose.vps.yml up -d
docker compose -f docker-compose.vps.yml ps
```

## 3. Полный V57 backfill + profile + train

```bash
docker compose -f docker-compose.vps.yml exec -T crypto-bot \
  python3 bootstrap_profitability_v57.py --limit 20000
```

Важно: первый backfill может занять заметное время из-за replay Shadow candles.

## 4. Проверить dataset

```bash
docker compose -f docker-compose.vps.yml exec -T crypto-bot \
  python3 audit_execution_v57.py
```

Нормальный результат должен содержать и `filled`, и `no_fill`. Execution ML дополнительно требует по умолчанию минимум 80 filled, 40 no-fill и 120 resolved outcomes. Если баланс не выполнен, обучение вернёт `invalid-label-balance` и останется fail-closed.

## 5. Логи

```bash
docker compose -f docker-compose.vps.yml logs -f --tail=200 crypto-bot
```

До прохождения profitability gate `NO_TRADE`/Shadow-only — ожидаемое безопасное поведение, а не ошибка.
