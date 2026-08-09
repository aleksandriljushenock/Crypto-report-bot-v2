# Crypto Report Bot v24 — Final Refactor

## Цель

Финальный рефакторинг выполнен без переписывания торговой стратегии с нуля. Основная цель — сохранить поведение фильтров и сигналов, убрать дублирование состояния/конфигурации, сделать исполнение Paper Trading идемпотентным и отделить Telegram UI от бизнес-логики.

## Новая структура

- `core/` — runtime config, единое состояние процессов, event stream.
- `exchanges/` — единый registry 10 бирж и typed capability contract.
- `scanner/` — отдельные Universe / Analysis / Signals / Pipeline.
- `trading/` — канонические состояния сделки и правила валидности исполнения.
- `repositories/` — Supabase persistence boundary и reconciliation Paper account.
- `application/` — application services для UI/диагностики.
- `telegram_ui/` — transport, keyboards, renderers, router, system handlers.
- `trade_engine.py` — compatibility facade, чтобы старые импорты не ломались.

## Критические исправления

### 1. Supabase-настройки фактически обходились центральным config facade

В `core/runtime_config.py` использовалось несуществующее имя `SETTING_SPECS`; исключение молча подавлялось. Поэтому runtime config мог постоянно откатываться к ENV/default вместо активного значения из `strategy_settings`/Supabase.

Исправлено на `SPEC_BY_KEY`. Теперь ключи стратегии действительно читаются из централизованного источника.

### 2. Duplicate Paper fill

Два пересекающихся worker/tick могли увидеть один `pending_entry`. Старый fallback после неуспешного update позволял второму worker продолжить и ещё раз списать margin/entry fee.

Исправлено compare-and-set переходом `pending_entry -> open` под локальным lock. Если позицию уже обработал другой worker, второе списание не происходит.

### 3. Duplicate Paper close / PnL

Два фоновых цикла могли одновременно закрыть одну OPEN позицию, создать повторную запись trade и повторно начислить PnL/fees.

Исправлено compare-and-set `open -> closed`, уникальным `paper_trades.position_id` и idempotent upsert.

### 4. Неверный Equity при нескольких открытых позициях

При закрытии одной позиции `equity` приравнивался к free balance. Если оставались другие открытые позиции, зарезервированная маржа исчезала из account equity.

Теперь equity обновляется дельтой закрываемой позиции (`gross PnL - exit fee`) поверх предыдущего equity. Free balance и account equity снова имеют разные корректные смыслы.

### 5. Неизвестные биржевые метрики выглядели как numeric zero

Для отсутствующего OI/ratio/funding ноль может означать реальное значение, а не отсутствие данных. Это способно искажать AI/Smart Money признаки.

Добавлен capability contract: `supported`, `unavailable`, `unsupported`. Missing metric передаётся как error/capability, а не `0`.

### 6. Раздробленный Scanner

Большой `trade_engine` одновременно выбирал universe, загружал рынок, строил признаки, создавал signals и управлял runtime lock.

Разделено на `scanner/universe.py`, `scanner/analysis.py`, `scanner/signals.py`, `scanner/pipeline.py`. Старый API оставлен через facade для совместимости.

### 7. Telegram monolith

Routing, transport, rendering, runtime threads и бизнес-статусы жили в одном модуле. Это уже приводило к противоречивым статусам.

Вынесены `telegram_ui/client.py`, `keyboards.py`, `renderers.py`, `router.py`, `status_view.py`, `system_handlers.py`, а диагностика — в application service. `telegram_command_bot.py` оставлен entrypoint/runtime owner.

### 8. Chronos toggle переживал redeploy ненадёжно

Toggle мог храниться только в локальном runtime/SQLite и теряться на ephemeral Railway filesystem.

Теперь `CHRONOS_ENABLED` — Supabase-backed strategy setting. Есть процесс-local fallback только при недоступности cloud storage.

### 9. Profit profile мог не попасть в Docker image

Старый ignore исключал весь `data/`, включая `data/profit_profile_v2.json`, поэтому Hedge Engine мог тихо работать на generic fallback.

`.dockerignore` теперь исключает runtime data, но явно включает `data/profit_profile_v2.json`.

### 10. Supabase dashboard SECURITY DEFINER / view column conflicts

Финальная migration пересоздаёт `learning_signal_quality_dashboard` с `security_invoker=true`, избегая старого `CREATE OR REPLACE` конфликта колонок и SECURITY DEFINER риска.

## Paper Trading lifecycle

`Signal -> pending_entry -> open -> closed/cancelled`

Optimizer/Adaptive Model получают только реально исполненные CLOSED позиции с `fill_price_source`, положительными entry/margin и корректными timestamps. `INVALID_FILL`, missed/cancelled и phantom fills не используются как обучающие сделки.

`execution_audit` хранит signal price, target entry, actual fill, fill source и timestamps.

## Обязательная migration

Перед первым deploy v24 выполнить:

`migrations/SUPABASE_FINAL_REFACTOR_V24.sql`

Она добавляет execution audit, lifecycle indexes, идемпотентность `paper_trades.position_id` и безопасно пересоздаёт learning dashboard.

## Тестирование

Финальный локальный regression suite: 56 tests passed.

Дополнительно выполнен `compileall` для всего Python source tree. Проверены scanner contracts, 10-provider registry, missing exchange capability semantics, Paper CAS fill/close, equity accounting и Supabase-backed runtime config.

Live API smoke-test с реальными Railway/Supabase/Telegram credentials намеренно не выполнялся в сборочной среде: секреты в архив не включены. После deploy health/exchange status в Telegram является production smoke-test реальных внешних сервисов.
