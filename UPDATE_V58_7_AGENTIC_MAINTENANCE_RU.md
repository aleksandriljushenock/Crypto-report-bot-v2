# v58.7.0 — Guarded Agentic Maintenance

Репозиторий подготовлен к постоянной работе coding-agent'ов: единый AGENTS.md, автоматический аудит каждые 6 часов, agent-ready issues, обязательный CI, fail-closed release builder, guarded auto-merge для непроизводственных изменений, релизные ZIP по тегу и опциональный VPS deploy с health-check/rollback.

Автономность намеренно ограничена защитными границами. Изменения execution model, live execution, deployment, migrations, secrets и profitability gates требуют ручного review. Агенту запрещено ослаблять OOS/profitability gates ради champion.

Для production deploy GitHub Environment `production` должен содержать secrets VPS_HOST, VPS_USER и VPS_SSH_KEY. Без них deploy workflow не имеет доступа к VPS.
