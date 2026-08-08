# VPS Docker Production

Добавлено:

- `Dockerfile.vps` — long polling runtime для VPS;
- `docker-compose.vps.yml` — persistent data/logs, restart policy, Docker log rotation;
- `.env.vps.example` — безопасный шаблон VPS-переменных;
- `scripts/update.sh` — обновление одной командой;
- `scripts/backup.sh` — локальные backup без секретов;
- `scripts/install_backup_cron.sh` — ежедневный backup;
- `scripts/status.sh` — быстрый статус и логи;
- `/server` и кнопка `🖥 Сервер` в Telegram;
- `VPS_DOCKER_SETUP_RU.md` — пошаговая инструкция для Ubuntu VPS.

На VPS публичный HTTP endpoint не нужен: Telegram работает через long polling.
