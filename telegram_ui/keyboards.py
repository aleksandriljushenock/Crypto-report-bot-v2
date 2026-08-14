"""Pure Telegram keyboard definitions.

No market, persistence or runtime state is read here. This keeps navigation
stable and testable independently from trading logic.
"""
from __future__ import annotations


def back_row(target="menu_main"):
    return [{"text": "⬅️ Назад", "callback_data": target}]


def home_row():
    return [{"text": "🏠 Главное меню", "callback_data": "menu_main"}]


def main_keyboard():
    return {"inline_keyboard": [
        [{"text": "🔍 Сканировать сейчас", "callback_data": "trade_scan"}],
        [
            {"text": "🎯 Сигналы", "callback_data": "menu_signals"},
            {"text": "📈 Результаты", "callback_data": "menu_performance"},
        ],
        [
            {"text": "🧭 Стратегии", "callback_data": "menu_strategies"},
            {"text": "🤖 AI Центр", "callback_data": "menu_ai"},
        ],
        [
            {"text": "📊 Рынок", "callback_data": "menu_analytics"},
            {"text": "⚙️ Настройки", "callback_data": "menu_system"},
        ],
    ]}


def strategies_keyboard():
    from strategies.catalog import STRATEGIES
    rows = [[{"text": "🏆 Leaderboard", "callback_data": "strategy_leaderboard"}]]
    pending = []
    for spec in STRATEGIES:
        pending.append({"text": f"{spec.emoji} {spec.title}", "callback_data": f"strategy_{spec.short}"})
        if len(pending) == 2:
            rows.append(pending)
            pending = []
    if pending:
        rows.append(pending)
    rows.append(home_row())
    return {"inline_keyboard": rows}


def strategy_lab_keyboard(strategy):
    from strategies.catalog import get_strategy
    from strategy_settings import current_value
    spec = get_strategy(strategy)
    prefix = f"lab_{spec.short}"
    notify_key = f"STRATEGY_NOTIFY_{spec.key.upper()}"
    notify_on = str(current_value(notify_key)).lower() == "true"
    notify_text = "🔔 Уведомления: ВКЛ" if notify_on else "🔕 Уведомления: ВЫКЛ"
    return {"inline_keyboard": [
        [{"text": "🔍 Анализировать монеты", "callback_data": f"{prefix}_scan"}],
        [{"text": notify_text, "callback_data": f"{prefix}_notify"}],
        [
            {"text": "📈 Статистика", "callback_data": f"{prefix}_winrate"},
            {"text": "🟡 Кандидаты", "callback_data": f"{prefix}_candidates"},
        ],
        [
            {"text": "📜 История", "callback_data": f"{prefix}_history"},
            {"text": "🔄 Outcomes", "callback_data": f"{prefix}_outcomes"},
        ],
        [{"text": "📐 Правила стратегии", "callback_data": f"{prefix}_rules"}],
        [{"text": "⬅️ Стратегии", "callback_data": "menu_strategies"}],
        home_row(),
    ]}


def fib_strategy_keyboard():
    return strategy_lab_keyboard("fib_05_pullback")

def signals_keyboard():
    return {"inline_keyboard": [
        [
            {"text": "🔥 Последние", "callback_data": "recent_signals"},
            {"text": "💎 Лучший сигнал", "callback_data": "best_signal"},
        ],
        [
            {"text": "🧠 Почему AI выбрал", "callback_data": "explain_signal"},
            {"text": "⭐ Watchlist", "callback_data": "trade_watchlist"},
        ],
        [
            {"text": "📈 Точность сигналов", "callback_data": "trade_performance"},
            {"text": "📰 Новости", "callback_data": "ai_news"},
        ],
        [{"text": "⏱ Статус скана", "callback_data": "scan_status"}],
        home_row(),
    ]}


def market_keyboard():
    return {"inline_keyboard": [
        [{"text": "📊 Полный Market Report", "callback_data": "run_report"}],
        [
            {"text": "💰 Capital Flow", "callback_data": "capital_flows"},
            {"text": "😨 Fear & Greed", "callback_data": "sentiment"},
        ],
        [
            {"text": "🧠 Нарративы", "callback_data": "narratives"},
            {"text": "📰 Новости", "callback_data": "ai_news"},
        ],
        [{"text": "⬅️ Аналитика", "callback_data": "menu_analytics"}],
        home_row(),
    ]}


def trade_keyboard():
    return {"inline_keyboard": [
        [{"text": "⚡ Запустить скан", "callback_data": "trade_scan"}],
        [
            {"text": "▶️ Монитор ON", "callback_data": "monitor_on"},
            {"text": "⏸ Монитор OFF", "callback_data": "monitor_off"},
        ],
        [
            {"text": "📡 Статус монитора", "callback_data": "monitor_status"},
            {"text": "⏱ Статус скана", "callback_data": "scan_status"},
        ],
        [{"text": "🎯 К сигналам", "callback_data": "menu_signals"}],
        home_row(),
    ]}


def discovery_keyboard():
    return {"inline_keyboard": [
        [
            {"text": "🔭 Early Discovery", "callback_data": "early_discovery"},
            {"text": "🚨 Listing Hunter", "callback_data": "listing_hunter"},
        ],
        [{"text": "🆕 Обновить базу листингов", "callback_data": "scan_new_100"}],
        [{"text": "📈 Прогресс базы", "callback_data": "listing_progress"}],
        [{"text": "⬅️ Аналитика", "callback_data": "menu_analytics"}],
        home_row(),
    ]}


def ai_keyboard():
    return {"inline_keyboard": [
        [
            {"text": "🏆 Champion / Модель", "callback_data": "model_status"},
            {"text": "🧬 Learning", "callback_data": "self_learning"},
        ],
        [
            {"text": "🏆 TOP AI", "callback_data": "top_ai"},
            {"text": "📚 AI History", "callback_data": "ai_history"},
        ],
        [{"text": "🧠 Статус Chronos", "callback_data": "chronos_status"}],
        [
            {"text": "🟢 Chronos ON", "callback_data": "chronos_on"},
            {"text": "⚪ Chronos OFF", "callback_data": "chronos_off"},
        ],
        [{"text": "🧠 AI Optimizer", "callback_data": "ai_optimizer"}],
        [{"text": "🐋 Smart Money", "callback_data": "smart_money"}],
        home_row(),
    ]}


def performance_keyboard():
    return {"inline_keyboard": [
        [
            {"text": "📅 Сегодня", "callback_data": "perf_today"},
            {"text": "📆 7 дней", "callback_data": "perf_week"},
        ],
        [
            {"text": "🏆 Монеты", "callback_data": "perf_coins"},
            {"text": "🎚 Фильтры", "callback_data": "perf_filters"},
        ],
        [
            {"text": "📂 Открытые", "callback_data": "paper_positions"},
            {"text": "📜 Сделки", "callback_data": "paper_history"},
        ],
        [
            {"text": "🏁 Путь к 50", "callback_data": "paper_goal"},
            {"text": "🧪 Paper", "callback_data": "paper_menu"},
        ],
        home_row(),
    ]}


def analytics_keyboard():
    return {"inline_keyboard": [
        [
            {"text": "🔎 Сканер", "callback_data": "scanner_intelligence"},
            {"text": "🌍 Universe", "callback_data": "universe_dashboard"},
        ],
        [
            {"text": "🌡 Market Mood", "callback_data": "market_mood"},
            {"text": "🗺 Heat Map", "callback_data": "heat_map"},
        ],
        [
            {"text": "💎 Лучший сигнал", "callback_data": "best_signal"},
            {"text": "🏦 Биржи", "callback_data": "exchange_status"},
        ],
        [
            {"text": "🧩 Комбинации", "callback_data": "best_combos"},
            {"text": "🌍 Рынок + новости", "callback_data": "menu_market"},
        ],
        [{"text": "🔭 Discovery", "callback_data": "menu_discovery"}],
        home_row(),
    ]}


def system_keyboard():
    return {"inline_keyboard": [
        [{"text": "🎛 Настройки стратегии", "callback_data": "strategy_settings"}],
        [
            {"text": "📟 Состояние", "callback_data": "dashboard"},
            {"text": "❤️ Health", "callback_data": "health_check"},
        ],
        [
            {"text": "🧩 Сервисы", "callback_data": "automation_status"},
            {"text": "🖥 Сервер", "callback_data": "server_status"},
        ],
        [{"text": "🛠 Диагностика", "callback_data": "bot_status"}],
        home_row(),
    ]}


def strategy_settings_keyboard():
    return {"inline_keyboard": [
        [
            {"text": "🎯 Фильтры", "callback_data": "cfg_cat:filters"},
            {"text": "🧩 Веса правил", "callback_data": "cfg_cat:rules"},
        ],
        [
            {"text": "💵 Размер позиции", "callback_data": "cfg_cat:position"},
            {"text": "🕒 Свежесть", "callback_data": "cfg_cat:recency"},
        ],
        [{"text": "⚙️ Сканирование", "callback_data": "cfg_cat:runtime"}],
        [{"text": "🧪 Paper Trading", "callback_data": "cfg_cat:paper"}],
        [{"text": "🧠 AI Optimizer", "callback_data": "cfg_cat:optimizer"}],
        [{"text": "🔄 Загрузить из Supabase", "callback_data": "cfg_reload"}],
        back_row("menu_system"),
    ]}


def dashboard_keyboard():
    return {"inline_keyboard": [
        [{"text": "⚡ Запустить скан", "callback_data": "trade_scan"}],
        [
            {"text": "▶️ Монитор ON", "callback_data": "monitor_on"},
            {"text": "⏸ Монитор OFF", "callback_data": "monitor_off"},
        ],
        [
            {"text": "🔥 Сигналы", "callback_data": "recent_signals"},
            {"text": "📈 Результаты", "callback_data": "trade_performance"},
        ],
        [
            {"text": "🟢 Chronos ON", "callback_data": "chronos_on"},
            {"text": "⚪ Chronos OFF", "callback_data": "chronos_off"},
        ],
        [{"text": "🔄 Обновить панель", "callback_data": "dashboard"}],
        home_row(),
    ]}


def scanner_intelligence_keyboard():
    return {"inline_keyboard": [
        [{"text": "🔍 Новый скан", "callback_data": "trade_scan"}],
        [{"text": "🌍 Universe", "callback_data": "universe_dashboard"}],
        [{"text": "🟡 Near Signals", "callback_data": "near_signals"}, {"text": "👻 Shadow", "callback_data": "shadow_signals"}],
        [{"text": "📊 Рынок", "callback_data": "menu_analytics"}],
        home_row(),
    ]}


def universe_dashboard_keyboard():
    return {"inline_keyboard": [
        [{"text": "🔄 Обновить", "callback_data": "universe_dashboard"}],
        [{"text": "🏦 Биржи", "callback_data": "exchange_status"}],
        [{"text": "🔎 Сканер", "callback_data": "scanner_intelligence"}],
        home_row(),
    ]}


def exchange_status_keyboard():
    return {"inline_keyboard": [
        [{"text": "🔄 Проверить снова", "callback_data": "exchange_status"}],
        [{"text": "📊 Аналитика", "callback_data": "menu_analytics"}],
        home_row(),
    ]}


def paper_keyboard():
    return {"inline_keyboard": [
        [
            {"text": "📊 Статистика", "callback_data": "paper_status"},
            {"text": "📂 Открытые позиции", "callback_data": "paper_positions"},
        ],
        [{"text": "📜 История сделок", "callback_data": "paper_history"}],
        [
            {"text": "▶️ Paper ON", "callback_data": "paper_on"},
            {"text": "⏸ Paper OFF", "callback_data": "paper_off"},
        ],
        [{"text": "♻️ Сбросить баланс", "callback_data": "paper_reset_confirm"}],
        back_row("menu_performance"),
        home_row(),
    ]}
