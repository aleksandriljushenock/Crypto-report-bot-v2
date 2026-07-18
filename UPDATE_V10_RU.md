# Crypto Report Service v10 — финальный рефакторинг

## Выполнено

### Этап 1. Надёжность

- Добавлен единый HTTP-клиент `core/http_client.py`.
- Используются пул соединений, retries, backoff и единые таймауты.
- Telegram API переведён на общий HTTP-клиент.
- Добавлено централизованное логирование с ротацией файлов.
- Добавлен типизированный модуль настроек `core/settings.py`.
- Добавлена проверка обязательных параметров и создание каталогов `data`/`logs`.

### Этап 2. Производительность

- HTTP Session переиспользует соединения.
- Добавлен TTL-кэш для безопасных GET JSON запросов.
- Планировщик получил jitter, чтобы фоновые сервисы не запускали запросы одновременно.
- Остановка фоновых потоков теперь выполняется с ожиданием завершения.

### Этап 3. Архитектура

- Общая инфраструктура вынесена в пакет `core/`.
- Сохранена совместимость `alpha_engine_v2.py`/`alpha_engine_v3.py`; резервная копия вынесена в `legacy/`.
- Архивные реализации перенесены в `legacy/`.
- Добавлен `healthcheck.py` и Windows-скрипт `run_healthcheck.bat`.
- Старые точки входа сохранены, поэтому команды запуска не изменились.

## Обновление

1. Сохранить текущие `.env` и каталог `data`.
2. Распаковать архив поверх существующего проекта.
3. Выполнить:

```cmd
.venv\Scripts\activate
pip install -r requirements.txt
python -m compileall .
python healthcheck.py
python telegram_command_bot.py
```

## Новые параметры `.env`

```env
LOG_LEVEL=INFO
LOG_MAX_BYTES=5000000
LOG_BACKUP_COUNT=5
HTTP_CONNECT_TIMEOUT=8
HTTP_READ_TIMEOUT=35
HTTP_RETRIES=3
HTTP_BACKOFF_FACTOR=0.6
HTTP_CACHE_TTL_SECONDS=60
```
