# Crypto Report Service — обновление Early Discovery v3

## Что изменено

- Добавлен единый сборщик официальных страниц листингов: KuCoin, Bybit, Gate, HTX, OKX, Bitget и MEXC.
- Каждый источник работает независимо: ошибка/403 одной биржи не останавливает остальные.
- Добавлен `AI Alpha v3` с отдельными блоками VC, dilution/unlock proxy, social/developer momentum и smart-money mentions.
- Недоступные данные не получают положительные баллы; они снижают уверенность.
- Добавлен сбор исторических исходов через 1h, 24h, 7d и 30d.
- После накопления минимум 30 исходов за 7 дней включается простая адаптация весов.
- Telegram-кнопка `🔭 Early Discovery` продолжает использоваться без изменения callback.

## Новые файлы

- `exchange_announcement_sources.py`
- `intelligence_providers.py`
- `alpha_engine_v3.py`
- `outcome_tracker.py`
- `adaptive_weights.py`

## Замененные файлы

- `early_discovery_collector.py`
- `early_discovery_pipeline.py`
- `early_discovery_report.py`
- `early_discovery_database.py` (безопасное обновление UPSERT/счетчика новых записей)

## Установка

1. Скопируйте новые и замененные `.py` файлы в рабочую папку проекта.
2. Не заменяйте свой `.env`. В архиве его нет.
3. Выполните:

```cmd
pip install -r requirements.txt
python -m py_compile *.py
```

4. Перезапустите Telegram-бота:

```cmd
python telegram_command_bot.py
```

5. Нажмите `🔭 Early Discovery`.

## Новые базы

Создаются автоматически:

- `data\intelligence_history.db` — снимки social/developer метрик.
- `data\alpha_outcomes.db` — прогнозы и фактические исходы.

Существующая `data\early_discovery.db` сохраняется и продолжает использоваться.

## Ограничения

- MEXC может возвращать 403; это не считается общей ошибкой запуска.
- Точный календарь unlock, подтвержденные VC-раунды и on-chain smart money требуют специализированного провайдера. Текущая бесплатная версия использует консервативные эвристики и явно помечает ограничения.
- Парсеры официальных страниц могут потребовать обновления, если биржа изменит HTML.

## Strategy Lab notifications v33

После обновления до v33 один раз выполните `migrations/SUPABASE_STRATEGY_NOTIFICATIONS_V33.sql`.
Миграция добавляет durable-флаги доставки READY / FILLED / CLOSED уведомлений для `strategy_setups`.
