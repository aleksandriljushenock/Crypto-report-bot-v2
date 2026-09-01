# Деплой v56 на VPS через GitHub + Docker Compose

Проект: `/opt/crypto-report-bot`  
Compose: `docker-compose.vps.yml`  
Service: `crypto-bot`

## 1. Локально
Распаковать v56 поверх локального Git-репозитория, не перезаписывая собственный `.env` и `data/`.

Проверить:
```bash
cat VERSION
```
Должно быть `56.0.0`.

Затем:
```bash
git status
git add -A
git commit -m "Release v56 quality validation hardening"
git push origin main
```
Если рабочая ветка называется не `main`, использовать её имя.

## 2. VPS: резервная копия
```bash
cd /opt/crypto-report-bot
cp .env /root/crypto-report-bot.env.backup-$(date +%Y%m%d-%H%M%S)
cp -a data /root/crypto-report-bot-data-backup-$(date +%Y%m%d-%H%M%S)
```

## 3. Получить код
```bash
cd /opt/crypto-report-bot
git fetch --all --prune
git pull --ff-only
cat VERSION
```
Ожидается `56.0.0`.

Если `git pull` не обновил код:
```bash
git branch --show-current
git status
git rev-parse HEAD
git rev-parse origin/$(git branch --show-current)
```
Не делать `reset --hard`, пока не проверено, что на VPS нет нужных локальных изменений.

## 4. Supabase migration
Открыть `migrations/SUPABASE_EXECUTION_INTELLIGENCE_V56.sql`, скопировать целиком в Supabase SQL Editor и выполнить один раз. Скрипт идемпотентен.

Проверка:
```sql
select count(*) from execution_training_dataset_v56;
```

## 5. Полная пересборка Docker
```bash
cd /opt/crypto-report-bot
docker compose -f docker-compose.vps.yml down --remove-orphans
docker compose -f docker-compose.vps.yml build --no-cache --pull --progress=plain crypto-bot
```

До запуска production-контейнера проверить НОВЫЙ image:
```bash
docker compose -f docker-compose.vps.yml run --rm --no-deps crypto-bot cat /app/VERSION
```
Ожидается `56.0.0`.

И наличие v56-файлов:
```bash
docker compose -f docker-compose.vps.yml run --rm --no-deps crypto-bot ls -lh /app/backfill_execution_dataset_v56.py /app/execution_model_v56.py
```

## 6. Запуск
```bash
docker compose -f docker-compose.vps.yml up -d --force-recreate
docker compose -f docker-compose.vps.yml ps
docker compose -f docker-compose.vps.yml exec crypto-bot cat /app/VERSION
```
Последняя команда тоже должна вернуть `56.0.0`.

## 7. Backfill V56
Shadow labels v55 нельзя просто переносить: v56 исправляет timestamp/pagination и должен переиграть их.

```bash
docker compose -f docker-compose.vps.yml exec crypto-bot \
  python3 /app/backfill_execution_dataset_v56.py --limit 20000
```

Проверка в Supabase:
```sql
select count(*) from execution_training_dataset_v56;

select source, outcome, count(*)
from execution_training_dataset_v56
group by source, outcome
order by source, outcome;
```

## 8. Пересобрать Profit Profile
```bash
docker compose -f docker-compose.vps.yml exec crypto-bot \
  python3 /app/build_profit_profile.py
```

## 9. Обучить Execution ML V56
```bash
docker compose -f docker-compose.vps.yml exec crypto-bot \
  python3 -c "from execution_model_v56 import train; import pprint; pprint.pp(train(trigger='manual'))"
```
`shadow` не является ошибкой. Это означает, что модель не прошла untouched Champion holdout и не получила права влиять на live Probability.

## 10. Финальный restart и логи
```bash
docker compose -f docker-compose.vps.yml restart crypto-bot
docker compose -f docker-compose.vps.yml logs -f --tail=200 crypto-bot
```

Искать ошибки по словам `v56`, `execution`, `backfill`, `profile`, `champion`, `shadow`, `persistence`.
