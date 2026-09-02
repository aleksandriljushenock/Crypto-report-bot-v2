# Deploy v57.1 на VPS

1. Залить код в GitHub.
2. На VPS выполнить `git pull` и убедиться, что `cat VERSION` показывает `57.1.0`.
3. Пересобрать контейнер: `docker compose -f docker-compose.vps.yml down`, затем `build --no-cache`, затем `up -d`.
4. Повторный backfill `execution_training_dataset_v57` не требуется: v57.1 использует уже заполненную таблицу v57.
5. Запустить обучение: `docker compose -f docker-compose.vps.yml exec -T crypto-bot python3 -c "from execution_model_v57 import train; import pprint; pprint.pp(train(trigger='v57.1-fix'))"`.
6. Проверить, что `trained_models > 0`. Если `status=shadow`, это допустимо: означает, что модели обучились, но profit/OOS gates не пройдены.
