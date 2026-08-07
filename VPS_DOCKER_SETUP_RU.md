# Развёртывание Crypto Report Bot на любом Ubuntu VPS через Docker

Эта конфигурация запускает Telegram-бот в режиме long polling. Публичный домен, HTTPS, Nginx и открытый HTTP-порт не нужны.

## 1. Требования

- Ubuntu 22.04/24.04 LTS
- 2+ vCPU
- 4+ GB RAM (8 GB комфортно для Chronos)
- 20+ GB свободного диска
- исходящий доступ в интернет

## 2. Установка Docker на чистый Ubuntu

Самый быстрый вариант после клонирования проекта: `sudo ./scripts/bootstrap_ubuntu.sh`. Если проект ещё не клонирован, выполни команды ниже вручную.

Выполнить от root:

```bash
apt update && apt upgrade -y
apt install -y ca-certificates curl git ufw
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
```

## 3. Firewall

Боту не нужен входящий web-порт. Оставляем только SSH:

```bash
ufw allow OpenSSH
ufw default deny incoming
ufw default allow outgoing
ufw --force enable
ufw status
```

Перед `ufw enable` обязательно убедись, что SSH уже разрешён.

## 4. Клонирование проекта

```bash
mkdir -p /opt/crypto-report-bot
cd /opt
git clone <YOUR_GITHUB_REPOSITORY_URL> crypto-report-bot
cd /opt/crypto-report-bot
```

Если репозиторий приватный, используй GitHub deploy key или HTTPS token. Не записывай token в git remote URL навсегда.

## 5. Переменные окружения

```bash
cp .env.vps.example .env
nano .env
```

Минимально заполни:

- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- SUPABASE_URL
- SUPABASE_SERVICE_KEY

Права:

```bash
chmod 600 .env
```

Не добавляй `.env` в Git.

## 6. Перенос локального состояния

Supabase уже хранит cloud learning / strategy settings / paper trading. Для SQLite и локальных checkpoint-файлов можно скопировать текущую папку `data/` на VPS до первого запуска.

Пример с Windows PowerShell:

```powershell
scp -r .\data root@SERVER_IP:/opt/crypto-report-bot/
```

Если локальное состояние не переносить, бот восстановит облачные данные там, где код поддерживает cloud restore, но локальные SQLite-настройки могут стартовать заново.

## 7. Первый запуск

```bash
cd /opt/crypto-report-bot
docker compose -f docker-compose.vps.yml build
docker compose -f docker-compose.vps.yml up -d
```

Проверка:

```bash
docker compose -f docker-compose.vps.yml ps
docker compose -f docker-compose.vps.yml logs -f --tail=150 crypto-bot
```

При запуске `cloud_entrypoint.py` удалит старый Telegram webhook Render и переключит Telegram на long polling.

## 8. Автозапуск

`restart: unless-stopped` + включённый Docker daemon автоматически поднимут контейнер после reboot VPS.

Проверка:

```bash
systemctl is-enabled docker
```

## 9. Обновление проекта

```bash
cd /opt/crypto-report-bot
./scripts/update.sh
```

Скрипт выполняет git pull, rebuild и безопасный restart контейнера.

## 10. Backup локального состояния

Ручной:

```bash
./scripts/backup.sh
```

Ежедневный cron:

```bash
./scripts/install_backup_cron.sh
```

`.env` специально не попадает в backup.

## 11. Полезные команды

```bash
./scripts/status.sh

docker compose -f docker-compose.vps.yml restart crypto-bot

docker compose -f docker-compose.vps.yml stop

docker compose -f docker-compose.vps.yml up -d

docker stats crypto-report-bot
```

## 12. После успешной миграции

1. Проверить `/start`, `/server`, Paper Trading, Supabase и биржи.
2. Убедиться, что на Render больше не работает второй экземпляр Telegram-бота.
3. Только после проверки удалить/остановить Render service.

Не держи Render и VPS одновременно в polling/webhook режиме для одного Telegram token: два экземпляра будут конкурировать за обновления.
