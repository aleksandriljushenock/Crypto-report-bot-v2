# Список изменений и исправлений

## Изменения
- Добавлен совместимый модуль Learning MAX 2.0 поверх существующего v14.
- Добавлены Feature Store, online/incremental observations и хранение реального результата.
- Добавлен ансамбль Trend/Momentum/Breakout/Reversal/Volatility/Risk.
- Добавлены feature importance, auto feature selection, calibration, uncertainty и XAI.
- Расширен Smart Money Engine единым нормализованным score.
- Переработан News Intelligence: удаление дублей, sentiment, impact, topics.
- Добавлены Telegram-команды `/market`, `/regime`, `/confidence`, `/features`, `/health`.
- Расширена схема `trade_signals` без разрушения существующих таблиц.
- HTTP-доступ новых модулей переведён на существующий retry/caching клиент.

## Исправления
- Исключено завершение новых аналитических цепочек при отказе RSS/API.
- Добавлен локальный fallback при сбое Learning MAX/GPT/API.
- Исправлено неполное сохранение probability/confidence/uncertainty/regime.
- Добавлена явная дедупликация новостей по нормализованному fingerprint.
- Устранено прямое использование `requests.get` в обновлённых news/smart-money модулях.
- Добавлены автономные smoke-тесты и расширенная проверка импортов.

## Зависимости
Новых обязательных зависимостей не добавлено. Используются уже заявленные в `requirements.txt`: requests, python-dotenv, beautifulsoup4, feedparser, flask.
