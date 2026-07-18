# Развёртывание Telegram-бота в Railway

Проект подготовлен для запуска как постоянный worker через Docker и Telegram long polling. Публичный HTTP-домен не требуется.

## 1. Загрузить проект в GitHub

Не добавляй `.env`, базы данных и секретные ключи. Они исключены через `.gitignore` и `.dockerignore`.

## 2. Создать Railway Project

1. Создай новый проект из GitHub-репозитория.
2. Railway обнаружит `Dockerfile` и соберёт контейнер.
3. Оставь **одну replica**. Telegram long polling не должен одновременно работать в нескольких экземплярах.

## 3. Добавить Variables

Обязательные:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Для Supabase learning:

```env
SUPABASE_URL=https://PROJECT.supabase.co
SUPABASE_SERVICE_KEY=...
SUPABASE_MODEL_BUCKET=models
```

Дополнительные:

```env
OPENAI_API_KEY=...
COINGECKO_API_KEY=...
COINMARKETCAP_API_KEY=...
ENABLED_EXCHANGES=binance,mexc,bybit,okx,kucoin,gate,bitget
EXCHANGE_QUOTES=USDT,USDC
DATA_DIR=/app/data
LOG_DIR=/app/logs
TZ=Europe/Moscow
```

`SUPABASE_KEY` также поддерживается как совместимый alias, но для нового деплоя лучше использовать `SUPABASE_SERVICE_KEY`.

## 4. Подключить persistent volume

Создай Railway Volume и смонтируй его в:

```text
/app/data
```

Там хранятся SQLite-базы, состояние мониторинга, outcomes, watchlist и кэш листингов. Без volume эти данные могут быть потеряны при пересоздании контейнера.

Логи одновременно выводятся в Railway Logs и записываются в `/app/logs`. Постоянный volume для логов не обязателен.

## 5. Запуск

Контейнер запускает:

```bash
python cloud_entrypoint.py
```

Точка входа:

- создаёт рабочие каталоги;
- проверяет обязательные Telegram variables;
- блокирует запуск второго экземпляра на том же volume;
- корректно обрабатывает `SIGTERM` при redeploy;
- запускает существующий `telegram_command_bot.listen()`.

## 6. Проверка

В Railway Logs должны появиться строки:

```text
Telegram command bot запущен.
Разрешенный chat_id: ...
```

После этого отправь боту `/start` и `/status`.

## Обновление

После push в подключённую ветку Railway автоматически соберёт и развернёт новую версию. SQLite-файлы сохранятся в `/app/data`.

## Важные ограничения

- Используй одну replica.
- Не запускай одновременно локальную копию с тем же Telegram token: Telegram будет возвращать conflict для `getUpdates`.
- Не помещай реальные секреты в GitHub.
- При изменении пути volume оставь `DATA_DIR` согласованным с mount path.
