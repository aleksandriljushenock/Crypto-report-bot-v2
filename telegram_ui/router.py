"""Telegram update router.

Routing is separated from runtime/thread ownership. The compatibility bot module
provides actions/state explicitly on every call, so the router does not own
trading state or background workers.
"""
from __future__ import annotations

_BOT_EXPORTS = ['CATEGORY_TITLES', 'SPEC_BY_KEY', '_chronos_state_text', '_fmt_metric', '_period_rows', 'ai_keyboard', 'ai_optimizer_keyboard', 'analytics_keyboard', 'apply_recommendation', 'automation_supervisor', 'build_ai_history_report', 'build_ai_optimizer_text', 'build_automation_status', 'build_best_combos_text', 'build_best_signal_text', 'build_capital_flow_report', 'build_coin_performance_text', 'build_exchange_status_text', 'build_explain_signal_text', 'build_filter_performance_text', 'build_heat_map_text', 'build_home_text', 'build_learning_report', 'build_listing_progress_message', 'build_market_mood_text', 'build_model_status_report', 'build_narrative_report', 'build_near_signal_text', 'build_news_report', 'build_paper_goal_text', 'build_paper_history_text', 'build_paper_positions_text', 'build_paper_status_text', 'build_performance_center_text', 'build_period_performance_text', 'build_professional_report', 'build_scanner_intelligence_text', 'build_sentiment_report', 'build_shadow_signals_text', 'build_smart_money_report', 'build_strategy_category_text', 'build_strategy_edit_text', 'build_strategy_settings_text', 'build_top_ai_report', 'build_universe_dashboard_text', 'disable_monitor', 'enable_monitor', 'ensure_paper_account', 'exc', 'exchange_status_keyboard', 'get_monitor_settings', 'handle_command', 'handle_system_callback', 'html', 'is_authorized', 'load_strategy_settings', 'log', 'main_keyboard', 'market_keyboard', 'new_scan_thread', 'paper_keyboard', 'performance_keyboard', 'portfolio_keyboard', 'portfolio_report', 'reject_recommendation', 'report_thread', 'reset_paper_account', 'reset_strategy_setting', 'run_optimizer', 'save_strategy_setting', 'scanner_intelligence_keyboard', 'send_message', 'send_monitor_status', 'send_recent_signals', 'send_trade_performance', 'send_v8_report', 'send_watchlist', 'signals_keyboard', 'start_early_discovery', 'start_listing_hunter', 'start_new_listings_scan', 'start_report', 'start_trade_scan', 'strategy_category_keyboard', 'strategy_edit_keyboard', 'strategy_edit_pending', 'strategy_settings_keyboard', 'system_keyboard', 'telegram_request', 'trade_keyboard', 'trade_monitor', 'trade_scan_thread', 'train_candidate', 'universe_dashboard_keyboard']

def process_update(update, bot):
    target = globals()
    for name in _BOT_EXPORTS:
        if hasattr(bot, name):
            target[name] = getattr(bot, name)
    return _process_update(update)

def _process_update(update):
    callback = update.get("callback_query")

    if callback:
        callback_id = callback.get("id")
        callback_data = callback.get("data")
        callback_message = callback.get("message", {})
        chat_id = callback_message.get("chat", {}).get("id")

        log(
            f"Получен callback: "
            f"data={callback_data}, chat_id={chat_id}"
        )

        if chat_id is None:
            log("Callback без chat_id")
            return

        if not is_authorized(chat_id):
            log(
                f"Отклонен callback от chat_id={chat_id}"
            )

            try:
                telegram_request(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": "Нет доступа",
                        "show_alert": True,
                    },
                    timeout=10,
                )
            except Exception as exc:
                log(
                    f"Ошибка answerCallbackQuery: {exc}"
                )

            return

        try:
            telegram_request(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                },
                timeout=10,
            )
        except Exception as exc:
            log(
                f"Не удалось закрыть callback: {exc}"
            )

        system_flags = {
            "monitor_enabled": bool(get_monitor_settings().get("enabled")),
            "monitor_alive": bool(trade_monitor is not None and trade_monitor.is_running()),
            "manual_thread_alive": bool(trade_scan_thread is not None and trade_scan_thread.is_alive()),
            "report_alive": bool(report_thread is not None and report_thread.is_alive()),
            "listing_alive": bool(new_scan_thread is not None and new_scan_thread.is_alive()),
        }
        if handle_system_callback(callback_data, chat_id, flags=system_flags, chronos_text=_chronos_state_text()):
            return

        if callback_data == "menu_main":
            send_message(chat_id, build_home_text(), reply_markup=main_keyboard())
            return

        if callback_data == "menu_signals":
            send_message(chat_id, "🎯 <b>СИГНАЛЫ</b>\nПоследние входы, причины выбора и результативность.", reply_markup=signals_keyboard())
            return

        if callback_data == "menu_analytics":
            send_message(chat_id, "📊 <b>АНАЛИТИКА</b>\nРынок, комбинации и статистика стратегии.", reply_markup=analytics_keyboard())
            return

        if callback_data == "menu_market":
            send_message(chat_id, "🌍 <b>РЫНОК И НОВОСТИ</b>\nДополнительные рыночные инструменты по запросу.", reply_markup=market_keyboard())
            return

        if callback_data == "menu_trade":
            send_message(chat_id, "📡 <b>МОНИТОРИНГ</b>\nУправление автоматическим и ручным сканированием.", reply_markup=trade_keyboard())
            return

        if callback_data == "menu_ai":
            send_message(chat_id, "🤖 <b>AI ЦЕНТР</b>\nChampion, Learning, Chronos и AI-диагностика.", reply_markup=ai_keyboard())
            return

        if callback_data in {"menu_performance", "menu_portfolio"}:
            send_message(chat_id, build_performance_center_text(), reply_markup=performance_keyboard())
            return

        if callback_data == "menu_system":
            send_message(chat_id, "⚙️ <b>НАСТРОЙКИ</b>\nСтратегия и технические инструменты.", reply_markup=system_keyboard())
            return

        if callback_data == "strategy_settings":
            strategy_edit_pending.pop(str(chat_id), None)
            send_message(chat_id, build_strategy_settings_text(), reply_markup=strategy_settings_keyboard())
            return

        if callback_data == "cfg_reload":
            applied = load_strategy_settings(seed_missing=True)
            send_message(
                chat_id,
                f"🔄 Настройки загружены из Supabase: <b>{len(applied)}</b>",
                reply_markup=strategy_settings_keyboard(),
            )
            return

        if callback_data and callback_data.startswith("cfg_cat:"):
            category = callback_data.split(":", 1)[1]
            if category not in CATEGORY_TITLES:
                send_message(chat_id, "⚠️ Неизвестный раздел.", reply_markup=strategy_settings_keyboard())
                return
            strategy_edit_pending.pop(str(chat_id), None)
            send_message(
                chat_id,
                build_strategy_category_text(category),
                reply_markup=strategy_category_keyboard(category),
            )
            return

        if callback_data and callback_data.startswith("cfg_edit:"):
            key = callback_data.split(":", 1)[1]
            if key not in SPEC_BY_KEY:
                send_message(chat_id, "⚠️ Неизвестный параметр.", reply_markup=strategy_settings_keyboard())
                return
            strategy_edit_pending[str(chat_id)] = key
            send_message(chat_id, build_strategy_edit_text(key), reply_markup=strategy_edit_keyboard(key))
            return

        if callback_data and callback_data.startswith("cfg_reset:"):
            key = callback_data.split(":", 1)[1]
            try:
                value = reset_strategy_setting(key, updated_by=f"telegram:{chat_id}")
                spec = SPEC_BY_KEY[key]
                text = f"✅ <b>{html.escape(spec.title)}</b> сброшен: <code>{html.escape(value)}</code>"
                keyboard = strategy_category_keyboard(spec.category)
            except Exception as exc:
                text = f"❌ Не удалось сбросить параметр: <code>{html.escape(str(exc)[:400])}</code>"
                keyboard = strategy_settings_keyboard()
            strategy_edit_pending.pop(str(chat_id), None)
            send_message(chat_id, text, reply_markup=keyboard)
            return

        if callback_data == "best_signal":
            send_message(chat_id, build_best_signal_text(), reply_markup=signals_keyboard())
            return

        if callback_data == "explain_signal":
            send_message(chat_id, build_explain_signal_text(), reply_markup=signals_keyboard())
            return

        if callback_data == "market_mood":
            send_message(chat_id, build_market_mood_text(), reply_markup=analytics_keyboard())
            return

        if callback_data == "heat_map":
            send_message(chat_id, build_heat_map_text(), reply_markup=analytics_keyboard())
            return

        if callback_data == "best_combos":
            send_message(chat_id, build_best_combos_text(), reply_markup=analytics_keyboard())
            return

        if callback_data == "scanner_intelligence":
            send_message(chat_id, build_scanner_intelligence_text(), reply_markup=scanner_intelligence_keyboard())
            return

        if callback_data == "universe_dashboard":
            send_message(chat_id, build_universe_dashboard_text(), reply_markup=universe_dashboard_keyboard())
            return

        if callback_data == "near_signals":
            send_message(chat_id, build_near_signal_text(), reply_markup=scanner_intelligence_keyboard())
            return

        if callback_data == "shadow_signals":
            send_message(chat_id, build_shadow_signals_text(), reply_markup=scanner_intelligence_keyboard())
            return

        if callback_data == "exchange_status":
            send_message(chat_id, build_exchange_status_text(active_probe=True), reply_markup=exchange_status_keyboard())
            return

        if callback_data == "perf_today":
            send_message(chat_id, build_period_performance_text("📅 <b>СЕГОДНЯ (UTC)</b>", _period_rows(today=True)), reply_markup=performance_keyboard())
            return

        if callback_data == "perf_week":
            send_message(chat_id, build_period_performance_text("📆 <b>ПОСЛЕДНИЕ 7 ДНЕЙ</b>", _period_rows(days=7)), reply_markup=performance_keyboard())
            return

        if callback_data == "perf_coins":
            send_message(chat_id, build_coin_performance_text(), reply_markup=performance_keyboard())
            return

        if callback_data == "perf_filters":
            send_message(chat_id, build_filter_performance_text(), reply_markup=performance_keyboard())
            return

        if callback_data == "paper_goal":
            send_message(chat_id, build_paper_goal_text(), reply_markup=portfolio_keyboard())
            return

        if callback_data == "scan_status":
            from trade_engine import is_trade_scan_running, get_trade_scan_runtime_state
            engine_busy = is_trade_scan_running()
            manual_busy = trade_scan_thread is not None and trade_scan_thread.is_alive()
            if engine_busy or manual_busy:
                st = get_trade_scan_runtime_state()
                owner = st.get('owner') or ('manual' if manual_busy else 'unknown')
                owner_text = 'фоновый монитор' if owner == 'monitor' else ('ручной скан' if owner == 'manual' else 'другой цикл')
                phase_map = {'universe':'сбор Universe', 'market_data':'загрузка рынка', 'analysis':'анализ монет', 'ranking':'AI/ранжирование'}
                phase = phase_map.get(st.get('phase'), st.get('phase') or 'работа')
                processed = int(st.get('processed') or 0)
                total = int(st.get('total') or 0)
                progress = f"\nПрогресс: <b>{processed}/{total}</b> монет" if total else ''
                text = f"⏳ <b>Сканер занят</b>\nИсточник: <b>{owner_text}</b>\nЭтап: <b>{phase}</b>{progress}"
            else:
                text = "✅ <b>Сканер готов</b>\nАктивного ручного или фонового прохода нет. Можно запустить новый поиск входов."
            send_message(chat_id, text, reply_markup=trade_keyboard())
            return

        if callback_data == "paper_menu":
            ensure_paper_account()
            send_message(chat_id, build_paper_status_text(), reply_markup=paper_keyboard())
            return

        if callback_data == "paper_status":
            send_message(chat_id, build_paper_status_text(), reply_markup=paper_keyboard())
            return

        if callback_data == "paper_positions":
            send_message(chat_id, build_paper_positions_text(), reply_markup=paper_keyboard())
            return

        if callback_data == "paper_history":
            send_message(chat_id, build_paper_history_text(), reply_markup=paper_keyboard())
            return

        if callback_data == "paper_on":
            try:
                save_strategy_setting("PAPER_TRADING_ENABLED", True, updated_by=f"telegram:{chat_id}")
                text = "🟢 <b>Paper Trading включён</b>\nНовые финальные сигналы будут открывать виртуальные позиции."
            except Exception as exc:
                text = f"❌ Ошибка: <code>{html.escape(str(exc)[:300])}</code>"
            send_message(chat_id, text, reply_markup=paper_keyboard())
            return

        if callback_data == "paper_off":
            try:
                save_strategy_setting("PAPER_TRADING_ENABLED", False, updated_by=f"telegram:{chat_id}")
                text = "⚪ <b>Paper Trading выключен</b>\nНовые позиции не открываются. Уже открытые продолжают отслеживаться."
            except Exception as exc:
                text = f"❌ Ошибка: <code>{html.escape(str(exc)[:300])}</code>"
            send_message(chat_id, text, reply_markup=paper_keyboard())
            return

        if callback_data == "paper_reset_confirm":
            send_message(
                chat_id,
                "⚠️ <b>Сбросить paper-счёт?</b>\nБудут удалены история и статистика. Сброс невозможен при открытых позициях.",
                reply_markup={"inline_keyboard": [
                    [{"text": "✅ Да, сбросить до $100", "callback_data": "paper_reset_execute"}],
                    [{"text": "❌ Отмена", "callback_data": "paper_menu"}],
                ]},
            )
            return

        if callback_data == "paper_reset_execute":
            result = reset_paper_account(100.0)
            if result.get("status") == "reset":
                text = "✅ Paper-счёт сброшен. Баланс: <b>$100.00</b>."
            elif result.get("status") == "open-positions-exist":
                text = "❌ Сначала дождись закрытия всех paper-позиций."
            else:
                text = "❌ Не удалось сбросить paper-счёт. Проверь Render logs."
            send_message(chat_id, text, reply_markup=paper_keyboard())
            return

        if callback_data == "ai_optimizer":
            send_message(chat_id, build_ai_optimizer_text(), reply_markup=ai_optimizer_keyboard())
            return

        if callback_data == "optimizer_run":
            result = run_optimizer(trigger=f"telegram:{chat_id}")
            send_message(
                chat_id,
                f"✅ <b>AI Optimizer завершён</b>\nСделок: <b>{result.get('samples',0)}</b>\nРекомендаций: <b>{result.get('recommendations_count',0)}</b>",
                reply_markup=ai_optimizer_keyboard(),
            )
            return

        if callback_data == "adaptive_train":
            result = train_candidate(trigger=f"telegram:{chat_id}")
            met = result.get('metrics') or {}
            text = (
                "🧬 <b>Adaptive Model</b>\n\n"
                f"Статус: <b>{html.escape(str(result.get('status')))}</b>\n"
                f"Версия: <code>{html.escape(str(result.get('version') or '—'))}</code>\n"
                f"Train: <b>{result.get('samples_train',0)}</b> · Validation: <b>{result.get('samples_validation',0)}</b>\n"
                f"LogLoss: <b>{_fmt_metric(met.get('log_loss'),3)}</b> · baseline: <b>{_fmt_metric(met.get('baseline_log_loss'),3)}</b>"
            )
            send_message(chat_id, text, reply_markup=ai_optimizer_keyboard())
            return

        if callback_data.startswith("opt_apply:"):
            rid = callback_data.split(":",1)[1]
            result = apply_recommendation(rid, updated_by=f"telegram:{chat_id}")
            text = "✅ Рекомендация применена." if result.get('status') == 'applied' else f"⚠️ {html.escape(str(result.get('status')))}"
            send_message(chat_id, text, reply_markup=ai_optimizer_keyboard())
            return

        if callback_data.startswith("opt_reject:"):
            rid = callback_data.split(":",1)[1]
            reject_recommendation(rid, updated_by=f"telegram:{chat_id}")
            send_message(chat_id, "✖️ Рекомендация отклонена.", reply_markup=ai_optimizer_keyboard())
            return

        if callback_data == "chronos_on":
            try:
                from chronos_forecaster import set_chronos_enabled
                enabled = set_chronos_enabled(True)
                text = (
                    "🟢 <b>Chronos включён</b>\n"
                    "Будет применяться только к финальному кандидату и только если memory guard разрешит запуск."
                    if enabled else "⚠️ Не удалось включить Chronos."
                )
            except Exception as exc:
                text = f"❌ Ошибка включения Chronos: <code>{str(exc)[:300]}</code>"
            send_message(chat_id, text, reply_markup=ai_keyboard())
            return

        if callback_data == "chronos_off":
            try:
                from chronos_forecaster import set_chronos_enabled
                set_chronos_enabled(False)
                text = "⚪ <b>Chronos выключен</b>\nСканирование продолжит работать без модели Chronos."
            except Exception as exc:
                text = f"❌ Ошибка выключения Chronos: <code>{str(exc)[:300]}</code>"
            send_message(chat_id, text, reply_markup=ai_keyboard())
            return

        if callback_data == "chronos_status":
            send_message(
                chat_id,
                f"🧠 <b>Chronos</b>\nТекущее состояние: <b>{_chronos_state_text()}</b>\n\n"
                "Настройка сохраняется для текущего экземпляра Render и после рестарта возвращается "
                "к значению CHRONOS_ENABLED из ENV.",
                reply_markup=ai_keyboard(),
            )
            return

        if callback_data == "model_status":
            send_v8_report(chat_id, build_model_status_report)
            return

        if callback_data == "portfolio_help":
            send_message(
                chat_id,
                "💼 <b>Управление портфелем</b>\n\n"
                "Добавить: <code>/portfolio_add BTC 0.1 60000</code>\n"
                "Удалить: <code>/portfolio_del BTC</code>\n"
                "Показать: <code>/portfolio</code>",
                reply_markup=portfolio_keyboard(),
            )
            return

        if callback_data == "trade_scan":
            start_trade_scan(chat_id)
            return

        if callback_data == "monitor_on":
            enable_monitor(chat_id)
            return

        if callback_data == "monitor_off":
            disable_monitor(chat_id)
            return

        if callback_data == "monitor_status":
            send_monitor_status(chat_id)
            return

        if callback_data == "recent_signals":
            send_recent_signals(chat_id)
            return

        if callback_data == "trade_watchlist":
            send_watchlist(chat_id)
            return

        if callback_data == "trade_performance":
            send_trade_performance(chat_id)
            return

        if callback_data == "top_ai":
            send_v8_report(chat_id, build_top_ai_report)
            return
        if callback_data == "ai_history":
            send_message(chat_id, build_ai_history_report(), reply_markup=ai_keyboard())
            return
        if callback_data == "pro_report":
            send_v8_report(chat_id, build_professional_report)
            return
        if callback_data == "capital_flows":
            send_v8_report(chat_id, build_capital_flow_report)
            return
        if callback_data == "smart_money":
            send_v8_report(chat_id, build_smart_money_report)
            return
        if callback_data == "narratives":
            send_v8_report(chat_id, build_narrative_report)
            return
        if callback_data == "ai_news":
            send_v8_report(chat_id, build_news_report)
            return
        if callback_data == "sentiment":
            send_v8_report(chat_id, build_sentiment_report)
            return
        if callback_data == "portfolio":
            send_v8_report(chat_id, portfolio_report)
            return
        if callback_data == "self_learning":
            send_v8_report(chat_id, build_learning_report)
            return

        if callback_data == "automation_status":
            send_message(chat_id, build_automation_status(automation_supervisor), reply_markup=main_keyboard())
            return

        if callback_data == "run_report":
            log("Нажата кнопка обычного отчета")
            start_report(chat_id)
            return

        if callback_data == "scan_new_100":
            log(
                "Нажата кнопка обновления базы листингов"
            )
            start_new_listings_scan(chat_id)
            return

        if callback_data == "early_discovery":
            log(
                "Нажата кнопка Early Discovery"
            )

            start_early_discovery(
                chat_id
            )

            return

        if callback_data == "listing_hunter":
            log(
                "Нажата кнопка Listing Hunter"
            )

            start_listing_hunter(
                chat_id
            )

            return

        if callback_data == "listing_progress":
            log(
                "Нажата кнопка прогресса базы"
            )

            progress_message = (
                build_listing_progress_message()
            )

            send_message(
                chat_id,
                progress_message,
                reply_markup=main_keyboard(),
            )

            return


        log(
            f"Неизвестный callback_data: "
            f"{callback_data}"
        )

        send_message(
            chat_id,
            (
                "⚠️ Кнопка устарела.\n"
                "Отправь /start, чтобы обновить меню."
            ),
            reply_markup=main_keyboard(),
        )
        return

    message = update.get("message")

    if not message:
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "")

    if chat_id is None or not text:
        return

    if not is_authorized(chat_id):
        log(
            f"Отклонена команда от chat_id={chat_id}"
        )

        try:
            send_message(
                chat_id,
                "⛔ У тебя нет доступа к этому боту.",
            )
        except Exception:
            pass

        return

    pending_key = strategy_edit_pending.get(str(chat_id))
    if pending_key:
        if text.strip().lower() == "/cancel":
            spec = SPEC_BY_KEY[pending_key]
            strategy_edit_pending.pop(str(chat_id), None)
            send_message(
                chat_id,
                "↩️ Изменение отменено.",
                reply_markup=strategy_category_keyboard(spec.category),
            )
            return
        try:
            value = save_strategy_setting(
                pending_key,
                text.strip(),
                updated_by=f"telegram:{chat_id}",
            )
            spec = SPEC_BY_KEY[pending_key]
            strategy_edit_pending.pop(str(chat_id), None)
            send_message(
                chat_id,
                f"✅ <b>{html.escape(spec.title)}</b> обновлён: <code>{html.escape(value)}</code>\n"
                "Новое значение применяется к следующим расчётам.",
                reply_markup=strategy_category_keyboard(spec.category),
            )
        except Exception as exc:
            send_message(
                chat_id,
                f"❌ Некорректное значение: <code>{html.escape(str(exc)[:400])}</code>\n\n"
                "Попробуй ещё раз или отправь /cancel.",
                reply_markup=strategy_edit_keyboard(pending_key),
            )
        return

    if text.startswith("/"):
        log(
            f"Получена команда {text!r} "
            f"от chat_id={chat_id}"
        )
        handle_command(chat_id, text)
