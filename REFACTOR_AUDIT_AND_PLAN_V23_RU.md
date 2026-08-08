# Полный аудит и план рефакторинга — v23 Phase 1

## Итог аудита

Текущая версия функционально сильная, но проект вырос быстрее первоначальной архитектуры. Переписывать с нуля не нужно: торговая логика уже накопила много полезного поведения. Нужен поэтапный рефакторинг с сохранением результатов сканера и Paper Trading на каждом этапе.

### Что обнаружено

1. **Telegram-слой стал монолитом.** `telegram_command_bot.py` — более 3300 строк и одновременно содержит UI, запуск потоков, состояние, настройки, управление сервисами и часть бизнес-логики.
2. **Runtime-state раздроблен.** Состояние сканера, ручного потока, heavy-task lock и фоновых сервисов живёт в разных местах. Это уже приводило к противоречивым статусам в Telegram.
3. **Конфигурация раздроблена.** В 46 runtime-модулях есть прямые обращения к `os.getenv` (более 200 обращений), в то время как только часть параметров централизована в `strategy_settings.py`. Это создаёт риск расхождения ENV / default / Supabase.
4. **Persistence раздроблен.** Около 30 модулей напрямую знают о SQLite или Supabase. Есть несколько outcome/learning/store слоёв, которые исторически наращивались параллельно.
5. **Есть несколько поколений движков.** `learning_engine_v13/v14`, старые alpha/listing компоненты и version-docs ещё присутствуют. Часть старого кода действительно используется как fallback, поэтому удалять всё одним коммитом опасно.
6. **Архив был сильно раздут runtime-артефактами.** Исходная распакованная папка была около 283 MB: `data` ~183 MB, cache ~40 MB, вложенный ZIP ~42 MB, `.git` ~15 MB, pycache и logs.
7. **Критичная проблема deployment asset:** `ai_hedge_fund_engine.py` ожидает `data/profit_profile_v2.json`, но прежний `.dockerignore` исключал весь `data/`. При Docker deployment бот мог молча переходить на встроенный generic fallback-профиль вместо обученного profit profile.
8. **Секреты:** в присланном архиве присутствовал заполненный `.env`. Также `test_telegram.py` выполнял реальный сетевой запрос уже при импорте. Это опасно для GitHub/архивов и ломает обычный `pytest`.
9. **Тесты не были полностью изолированы.** Несколько `test_*.py` были скорее ручными smoke-скриптами: им требовались сеть/секреты уже на этапе collection. Один тест Scanner Intelligence содержал фиксированную дату и со временем начал падать.
10. **UI накопил небольшие дубли.** Например, в настройках стратегии была дважды добавлена кнопка `Свежесть`; верхнее системное меню дублировало Monitoring и Live Status.
11. **Exchange defaults частично устарели.** В некоторых fallback-значениях оставалось 5 бирж, хотя production universe уже рассчитан на 10. Ограничение `MULTI_EXCHANGE_MIN_VENUES` также оставалось максимум 5.
12. **Ошибки местами скрываются слишком широко.** В ключевых модулях есть много `except Exception`/silent fallback. Для внешних API это полезно, но критические ошибки pipeline иногда становятся трудно диагностируемыми.

## Целевая архитектура

```text
Telegram UI
    ↓
Application services
    ├─ ScannerService
    ├─ TradingService
    ├─ IntelligenceService
    └─ SystemService
          ↓
Domain
    ├─ Signal / NearSignal / ShadowSignal
    ├─ PendingEntry / Position / ClosedTrade
    └─ StrategyConfig
          ↓
Infrastructure
    ├─ Exchange adapters
    ├─ Supabase repositories
    ├─ Chronos worker
    └─ Runtime state / event log
```

Главный принцип: Telegram не рассчитывает торговую логику; биржи не знают о UI; стратегия не читает ENV по всему проекту; runtime-status имеет один источник истины.

## План

### Этап 1 — Core foundation + cleanup — ГОТОВО в этой сборке

- добавлен `core/runtime_state.py` как единый thread-safe registry текущей активности;
- `trade_engine` переведён на общий runtime-state без изменения публичного API;
- heavy background tasks теперь публикуют своё состояние туда же;
- Live Status показывает реальную heavy background task;
- системное меню упрощено: `Состояние / Health / Сервисы / Сервер / Диагностика`;
- отдельная верхнеуровневая кнопка Monitoring убрана, управление монитором осталось внутри Live Status;
- удалена дублирующая кнопка `Свежесть`;
- исправлены defaults под 10 бирж;
- `MULTI_EXCHANGE_MIN_VENUES` теперь допускает 1..10;
- исправлен Docker/Git packaging для `profit_profile_v2.json`;
- удалены runtime snapshots, локальные DB, cache, logs, `.git`, `.env`, вложенный ZIP, pycache и очевидные мусорные файлы из release-архива;
- исторические version-docs перенесены в `docs/history`;
- тесты перенесены в `tests/`;
- сетевые smoke-tests больше не выполняются на import/pytest collection;
- исправлен time-dependent Scanner Intelligence test;
- добавлены тесты Runtime State.

**Результат:** release-папка уменьшена примерно с 283 MB до ~4 MB без удаления production-кода и profit profile.

### Этап 2 — Config + Scanner + Exchange contracts

1. Создать единый `StrategyConfig/RuntimeConfig` facade.
2. Перенести активные scan/near/shadow/Chronos параметры в каталог SettingSpec и Supabase.
3. Постепенно убрать прямые `os.getenv` из Scanner/Hedge/Paper/Telegram.
4. Разбить `trade_engine.py` на `universe`, `fast_scan`, `deep_scan`, `signal_gate` сервисы при сохранении текущих функций-обёрток.
5. Ввести capability-модель бирж: `supported / unavailable / value`, чтобы отсутствие OI/long-short никогда не превращалось в искусственный `0`.
6. Центральный provider registry для всех 10 бирж; убрать повторяющиеся списки бирж из UI/engine/status.
7. Добавить parity tests: старый и новый pipeline получают один snapshot и обязаны выдавать одинаковые финальные сигналы.

### Этап 3 — Trading lifecycle + Persistence

1. Единая модель: `Signal → PendingEntry → FilledPosition → ClosedTrade`.
2. Отдельные типы `RejectedCandidate / NearSignal / ShadowSignal`.
3. Единый repository layer для Supabase; SQLite оставить только как явно локальный cache там, где он действительно нужен.
4. Объединить пересекающиеся outcome trackers и убрать двойной учёт.
5. Жёстко гарантировать: Optimizer/Adaptive Model обучаются только на валидных фактически заполненных сделках.
6. Добавить immutable execution audit: signal price, target entry, actual fill, slippage, provider, timestamps.
7. Миграции + reconciliation tool для проверки баланса/PnL после redeploy.

### Этап 4 — Telegram split + Observability + Final cleanup

1. Разделить `telegram_command_bot.py` на handlers / keyboards / renderers / application services.
2. Оставить Telegram как тонкий слой `callback → service → render`.
3. Ввести единый event stream: `SCAN_STARTED`, `FAST_SCAN_DONE`, `SIGNAL_CREATED`, `PAPER_FILLED`, `POSITION_CLOSED`, `MODEL_PROMOTED` и т.д.
4. Health/Diagnostics строить из runtime state + events, а не из независимых проверок.
5. После parity-тестов удалить действительно неиспользуемые legacy поколения модулей.
6. Финально сократить Settings и внутренние технические кнопки, не нужные обычному пользователю.

## Что специально НЕ менялось в Phase 1

- пороги Quality / Probability / EV / R/R;
- логика Near/Shadow;
- Paper execution;
- веса profit profile;
- количество анализируемых монет;
- Chronos decision logic;
- Supabase schema.

Это сделано намеренно: первый этап должен улучшить архитектуру и упаковку, не смешивая рефакторинг с изменением стратегии.

## Проверка Phase 1

- весь Python-код проходит `py_compile`;
- pytest: **49 passed**;
- секреты не включены в release archive;
- `data/profit_profile_v2.json` сохранён и теперь не исключается Docker build.

## Перед production deploy

Если заполненный `.env` когда-либо был отправлен в публичный GitHub или третьим лицам, нужно перевыпустить Telegram token и остальные реальные секреты из этого файла. В новую сборку `.env` намеренно не включён.
