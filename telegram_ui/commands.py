from __future__ import annotations
from telegram_ui.client import telegram_request

BOT_COMMANDS = [
    {"command": "start", "description": "Открыть главное меню"},
    {"command": "report", "description": "Запустить полный анализ рынка"},
    {"command": "status", "description": "Проверить состояние анализа"},
    {"command": "help", "description": "Показать доступные команды"},
    {"command": "progress", "description": "Показать прогресс базы листингов"},
    {"command": "hunter", "description": "Проверить новые анонсы листингов"},
    {"command": "trade", "description": "Найти торговые входы сейчас"},
    {"command": "monitor_on", "description": "Включить фоновый мониторинг"},
    {"command": "monitor_off", "description": "Остановить фоновый мониторинг"},
    {"command": "monitor_status", "description": "Статус фонового мониторинга"},
    {"command": "signals", "description": "Последние торговые сигналы"},
    {"command": "watchlist", "description": "AI Watchlist монет"},
    {"command": "performance", "description": "Эффективность сигналов"},
    {"command": "paper", "description": "Paper Trading и PnL"},
    {"command": "server", "description": "CPU, RAM, disk и VPS status"},
    {"command": "automation_status", "description": "Статус фоновых сервисов"},
    {"command": "pro", "description": "Полный Professional отчет"},
    {"command": "flows", "description": "Потоки капитала"},
    {"command": "smartmoney", "description": "Smart Money события"},
    {"command": "narratives", "description": "Актуальные нарративы"},
    {"command": "sentiment", "description": "Fear & Greed"},
    {"command": "news", "description": "Важные новости"},
    {"command": "portfolio", "description": "Портфель"},
    {"command": "learn", "description": "Самообучение и веса"},
    {"command": "topai", "description": "TOP AI монеты"},
    {"command": "aihistory", "description": "История AI Score монеты"},
]

def register_bot_commands():
    return telegram_request("setMyCommands", {"commands": BOT_COMMANDS}, timeout=20)
