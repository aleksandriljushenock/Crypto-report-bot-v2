# Подключение постоянного coding-agent

Репозиторий уже подготовлен: `AGENTS.md` задаёт правила, `agent-ready` issues создаются автоматически, CI проверяет изменения, а безопасные PR из веток `agent/*` могут автоматически сливаться после зелёного CI при наличии метки `agent-auto-merge`.

Остаётся один внешний шаг: подключить к GitHub coding-agent (Codex/Copilot/Cursor или другой сервис) с правами читать issues, создавать ветки и PR. Агент должен брать issues с меткой `agent-ready` и выполнять `AGENTS.md`.

Production deploy намеренно требует GitHub Environment `production` и secrets `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`. Изменения защищённых файлов не автосливаются.
