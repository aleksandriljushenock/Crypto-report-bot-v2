# v24.1 — Telegram command routing hotfix

Исправлена регрессия после финального рефакторинга v24, из-за которой текстовые Telegram-команды (`/start`, `/help` и остальные slash-команды) падали до передачи в `handle_command`.

## Причина
`telegram_ui/router.py` после разделения Telegram-монолита обращался к общему словарю `strategy_edit_pending` для каждого входящего текстового сообщения. Этот объект оставался в `telegram_command_bot.py`, но не был включён в список зависимостей, которые router импортирует из compatibility-модуля. В результате для обычного сообщения возникал `NameError: strategy_edit_pending is not defined` до обработки команды. Callback-кнопки могли продолжать работать, поэтому неисправность выглядела как проблема именно slash-команд.

## Исправления
- `strategy_edit_pending` добавлен в явный router dependency bridge.
- `/start` добавлен в `setMyCommands`, поэтому он снова отображается в списке команд Telegram.
- Добавлены регрессионные тесты `/start`, `/help` и регистрации `/start`.
- Полный suite после исправления: 59 passed.
- `compileall`: PASS.

Новых SQL-миграций и ENV не требуется. Достаточно redeploy этой сборки.
