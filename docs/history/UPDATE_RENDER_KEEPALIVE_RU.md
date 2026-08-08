# Render KeepAlive

Добавлен фоновый KeepAlive-сервис, который каждые 10 минут вызывает публичный endpoint `/wake`.

Переменные Render:

```env
KEEPALIVE_ENABLED=true
KEEPALIVE_INTERVAL_SECONDS=600
KEEPALIVE_INITIAL_DELAY_SECONDS=60
```

Опционально можно указать адрес вручную:

```env
KEEPALIVE_URL=https://crypto-report-bot.onrender.com/wake
```

Проверка:

- `GET /wake` — возвращает состояние runtime и KeepAlive.
- `GET /health` — содержит секцию `keepalive` со временем последнего успешного ping.

Важно: внутренний KeepAlive является best-effort. Если Render уже полностью остановил контейнер, внутренний поток тоже не работает. Для гарантированного режима 24/7 нужен платный always-on instance. Для бесплатного плана рекомендуется дополнительно настроить внешний uptime monitor на URL `/wake` с интервалом 5–10 минут.
