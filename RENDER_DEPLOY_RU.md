# Бесплатный запуск Telegram-бота на Render + Supabase

Эта версия работает как Render Web Service через Telegram webhook. Локальный компьютер после настройки не нужен.

## 1. Загрузить проект в GitHub

Создай приватный репозиторий и загрузи содержимое этой папки. Настоящий `.env` не коммить.

## 2. Создать сервис Render

1. В Render выбери **New → Blueprint**.
2. Подключи GitHub-репозиторий.
3. Render прочитает `render.yaml`.
4. У сервиса должна остаться одна instance: Gunicorn запускается с `--workers 1`, чтобы не дублировать фоновые задачи.

## 3. Добавить секретные переменные

В Render → Service → Environment добавь:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_WEBHOOK_SECRET=случайная_строка_не_короче_32_символов
CRON_SECRET=другая_случайная_строка_не_короче_32_символов
SUPABASE_URL=https://....supabase.co
SUPABASE_SERVICE_KEY=...
OPENAI_API_KEY=...
COINGECKO_API_KEY=...
```

`RENDER_EXTERNAL_URL` Render добавляет автоматически. Не задавай его вручную.

Дополнительные ключи перенеси из своего локального `.env`, если соответствующие функции их используют.

## 4. Дождаться первого деплоя

В логах должны появиться строки:

```text
Telegram webhook установлен
Автосервисы запущены
Render webhook runtime полностью инициализирован
```

Проверка браузером:

```text
https://ИМЯ-СЕРВИСА.onrender.com/health
```

Ответ должен содержать `"status":"healthy"` и URL webhook.

## 5. Проверить Telegram

Полностью останови локальную копию бота. Затем отправь облачному боту:

```text
/start
/status
```

Webhook устанавливается автоматически при каждом старте Render.

## 6. Бесплатный сервис и пробуждение

У бесплатного Web Service возможен сон при отсутствии HTTP-трафика. Telegram webhook разбудит сервис входящей командой, но первый ответ после сна может задержаться.

Для регулярных фоновых задач настрой внешний планировщик, например cron-job.org, на вызовы URL сервиса. Заголовок для задач:

```text
Authorization: Bearer ЗНАЧЕНИЕ_CRON_SECRET
```

Поддерживаемые POST endpoints:

```text
/tasks/outcomes
/tasks/trade-outcomes
/tasks/listings
/tasks/discovery
/tasks/capital-flows
/tasks/news
/tasks/narratives
/tasks/smart-money
/tasks/ai
/tasks/learning
```

Для простого пробуждения без секрета доступен:

```text
GET /wake
```

Критические результаты обучения и outcomes сохраняются в Supabase. Локальные SQLite-файлы на бесплатном Render являются временными и могут исчезать при новом деплое или перезапуске.

## 7. Безопасность

- Не публикуй `SUPABASE_SERVICE_KEY`, токен Telegram и секреты Cron/Webhook.
- Не запускай вторую копию бота с тем же токеном.
- Сервис должен иметь один Gunicorn worker.
- `/tasks/*` доступны только с `CRON_SECRET`.
- Telegram запросы проверяются через `X-Telegram-Bot-Api-Secret-Token`.

## Диагностика

### `TELEGRAM_BOT_TOKEN отсутствует`
Добавь переменную в Render Environment и сделай Manual Deploy.

### `/health` возвращает 503
Открой Logs. Чаще всего не задан токен, chat ID или URL сервиса.

### Бот долго отвечает в первый раз
Вероятно, бесплатный сервис спал и запускается после webhook-запроса.

### Фоновые задачи пропускаются
Используй внешний cron для `/tasks/*`; внутренние потоки не работают, пока бесплатный сервис спит.
