"""Telegram presentation layer for trading/analytics screens.

Renderers may read application/query services but never start threads, mutate
positions, or call Telegram transport.
"""
from __future__ import annotations
import html
from datetime import datetime, timedelta, timezone

from core.logging_setup import get_logger
from core.runtime_config import boolean, integer
from ai_optimizer import get_latest_recommendations
from adaptive_model_manager import latest_models
from strategy_settings import CATEGORY_TITLES, SPEC_BY_KEY, current_value as get_strategy_setting_value, settings_by_category
from paper_trading import (performance as get_paper_performance, get_open_positions as get_paper_positions, get_recent_trades as get_paper_trades)
from trade_signal_store import get_recent_signals
from trade_market_client import get_provider_health_snapshot, probe_provider_health, get_last_universe_summary

_logger = get_logger("telegram_renderers")


def _fmt_metric(v, digits=2):
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return "—"


def build_ai_optimizer_text():
    recs = get_latest_recommendations(8)
    models = latest_models(3)
    lines = ["🧠 <b>AI OPTIMIZER + ADAPTIVE MODELS</b>", ""]
    try:
        from paper_trading import get_recent_trades
        closed = len(get_recent_trades(1000))
    except Exception:
        closed = 0
    lines.append(f"Закрытых Paper-сделок: <b>{closed}</b>")
    lines.append(f"Optimizer: <b>{'готов к анализу' if closed >= integer('AI_OPTIMIZER_MIN_TRADES', 20, minimum=1) else 'накапливает данные'}</b>")
    lines.append(f"Adaptive model: <b>{'готов к обучению' if closed >= integer('ADAPTIVE_MODEL_MIN_TRADES', 40, minimum=1) else 'накапливает данные'}</b>")
    lines.append("")
    if models:
        champion = next((m for m in models if m.get('status') == 'champion'), None)
        if champion:
            met = champion.get('metrics') or {}
            lines += [
                f"🏆 Champion: <code>{html.escape(str(champion.get('version')))}</code>",
                f"Validation: <b>{champion.get('samples_validation') or 0}</b> · LogLoss: <b>{_fmt_metric(met.get('log_loss'),3)}</b>",
                "",
            ]
        else:
            lines += ["🏆 Adaptive Champion: <b>ещё не выбран</b>", ""]
    else:
        lines += ["🏆 Adaptive Champion: <b>ещё не обучался</b>", ""]
    lines.append(f"Рекомендаций на подтверждение: <b>{len(recs)}</b>")
    if recs:
        for i, rec in enumerate(recs[:5], 1):
            metrics = rec.get('metrics') or {}
            key = rec.get('setting_key') or rec.get('symbol') or rec.get('kind')
            proposed = rec.get('proposed_value')
            line = f"{i}. <b>{html.escape(str(key))}</b>"
            if proposed is not None:
                line += f" → <code>{html.escape(str(proposed))}</code>"
            lines.append(line)
            reason = str(rec.get('reason') or '')
            if reason:
                lines.append(f"   {html.escape(reason[:180])}")
            if metrics.get('estimated_pnl_delta') is not None:
                lines.append(f"   ΔPnL ≈ <b>{_fmt_metric(metrics.get('estimated_pnl_delta'))}$</b> · сохранено {metrics.get('retention_pct','—')}% сделок")
    else:
        lines.append("Новых рекомендаций пока нет. Это нормально: система не меняет стратегию без достаточной статистики.")
    lines += ["", "Автоприменение выключено: изменения стратегии подтверждаются вручную."]
    return "\n".join(lines)


def build_strategy_settings_text():
    lines = [
        "🎛 <b>НАСТРОЙКИ СТРАТЕГИИ</b>",
        "",
        "Значения хранятся в Supabase и применяются сразу, без деплоя.",
        "Render ENV используется как резерв при недоступности базы.",
        "",
        "Выбери раздел:",
    ]
    return "\n".join(lines)


def build_strategy_category_text(category):
    title = CATEGORY_TITLES.get(category, category)
    lines = [f"{title}", ""]
    for spec in settings_by_category(category):
        value = html.escape(get_strategy_setting_value(spec.key))
        lines.append(f"• <b>{html.escape(spec.title)}</b>: <code>{value}</code>")
    lines.extend(["", "Нажми параметр, чтобы изменить его."])
    return "\n".join(lines)


def build_strategy_edit_text(key):
    spec = SPEC_BY_KEY[key]
    value = html.escape(get_strategy_setting_value(key))
    bounds = []
    if spec.minimum is not None:
        bounds.append(f"min {spec.minimum:g}")
    if spec.maximum is not None:
        bounds.append(f"max {spec.maximum:g}")
    bounds_text = f" ({', '.join(bounds)})" if bounds else ""
    return (
        f"✏️ <b>{html.escape(spec.title)}</b>\n\n"
        f"Текущее значение: <code>{value}</code>\n"
        f"Тип: <code>{spec.kind}</code>{bounds_text}\n\n"
        f"{html.escape(spec.description)}\n\n"
        "Отправь новое значение обычным сообщением.\n"
        "Для отмены отправь <code>/cancel</code>."
    )


def _signal_payload(row):
    payload = row.get("payload") if isinstance(row, dict) else None
    return payload if isinstance(payload, dict) else {}


def _num(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _signal_quality(row):
    p = _signal_payload(row)
    return _num(p.get("qualityScore", p.get("quality_score", row.get("quality_score") if isinstance(row, dict) else 0)))


def _signal_probability(row):
    p = _signal_payload(row)
    return _num(p.get("calibratedProbability", p.get("probability", row.get("probability") if isinstance(row, dict) else 0)))


def _signal_ev(row):
    p = _signal_payload(row)
    return _num(p.get("expectedValuePct", p.get("expected_value_pct", 0)))


def _signal_direction(row):
    p = _signal_payload(row)
    return str(p.get("direction") or (row.get("direction") if isinstance(row, dict) else "") or "").upper()


def _signal_symbol(row):
    p = _signal_payload(row)
    return str(p.get("symbol") or (row.get("symbol") if isinstance(row, dict) else "") or "?").upper()


def _paper_snapshot():
    try:
        stats = get_paper_performance() or {}
        account = stats.get("account") or {}
        return {
            "balance": _num(stats.get("derived_free_balance"), account.get("balance") or 0),
            "equity": _num(stats.get("derived_equity"), account.get("equity") or 0),
            "initial": _num(account.get("initial_balance"), 100),
            "pnl": _num(stats.get("net_pnl"), account.get("realized_pnl") or 0),
            "closed": int(stats.get("closed_count") or 0),
            "open": len(stats.get("open_positions") or []),
            "pending": len(stats.get("pending_positions") or []),
            "win_rate": _num(stats.get("win_rate")),
            "pf": _num(stats.get("profit_factor")),
            "liquidations": int(stats.get("liquidations") or 0),
        }
    except Exception as exc:
        _logger.warning("Paper snapshot error: %s", exc)
        return {"balance": 0, "initial": 100, "pnl": 0, "closed": 0, "open": 0, "win_rate": 0, "pf": 0}


def build_best_signal_text():
    rows = get_recent_signals(limit=50)
    if not rows:
        return "💎 <b>ЛУЧШИЙ СИГНАЛ</b>\n\nСигналов пока нет."
    best = max(rows, key=lambda r: (_signal_quality(r), _signal_ev(r), _signal_probability(r)))
    p = _signal_payload(best)
    symbol = html.escape(_signal_symbol(best))
    direction = "SHORT" if "SHORT" in _signal_direction(best) else "LONG"
    score = _num(p.get("score", best.get("score", 0)))
    quality = _signal_quality(best)
    prob = _signal_probability(best)
    ev = _signal_ev(best)
    rr = _num(p.get("rr", best.get("rr", 0)))
    return (
        "💎 <b>ЛУЧШИЙ СИГНАЛ ИЗ ПОСЛЕДНИХ 50</b>\n\n"
        f"<b>{symbol} {direction}</b>\n"
        f"Score: <b>{score:.0f}</b>\n"
        f"Quality: <b>{quality:.1f}</b>\n"
        f"Probability: <b>{prob:.1f}%</b>\n"
        f"EV: <b>{ev:+.2f}%</b>\n"
        f"R/R: <b>{rr:.2f}</b>"
    )


def build_explain_signal_text():
    rows = get_recent_signals(limit=1)
    if not rows:
        return "🧠 <b>ПОЧЕМУ AI ВЫБРАЛ СИГНАЛ</b>\n\nСигналов пока нет."
    row = rows[0]
    p = _signal_payload(row)
    positive = p.get("positiveProfileHits") or p.get("positive_profile_hits") or []
    anti = p.get("antiProfileHits") or p.get("anti_profile_hits") or []
    reasons = p.get("aiReasons") or p.get("qualityRules") or p.get("quality_rules") or []
    if isinstance(positive, str): positive = [positive]
    if isinstance(anti, str): anti = [anti]
    if isinstance(reasons, str): reasons = [reasons]
    lines = [
        "🧠 <b>ПОЧЕМУ AI ВЫБРАЛ ПОСЛЕДНИЙ СИГНАЛ</b>", "",
        f"<b>{html.escape(_signal_symbol(row))}</b> • Quality <b>{_signal_quality(row):.1f}</b> • EV <b>{_signal_ev(row):+.2f}%</b>", ""
    ]
    if positive:
        lines.append("✅ <b>Сильные профили</b>")
        lines.extend(f"• {html.escape(str(x))}" for x in positive[:5])
    if reasons:
        lines.append("\n📌 <b>Ключевые причины</b>")
        lines.extend(f"• {html.escape(str(x))}" for x in reasons[:5])
    if anti:
        lines.append("\n⚠️ <b>Риски / анти-профили</b>")
        lines.extend(f"• {html.escape(str(x))}" for x in anti[:5])
    if not positive and not reasons and not anti:
        lines.append("Подробные причины не сохранены в payload этого сигнала. Базовые метрики доступны в карточке сигнала.")
    return "\n".join(lines)


def build_market_mood_text():
    rows = get_recent_signals(limit=30)
    if not rows:
        return "🌡 <b>MARKET MOOD</b>\n\nНедостаточно недавних сигналов."
    long_n = sum(1 for r in rows if "LONG" in _signal_direction(r))
    short_n = sum(1 for r in rows if "SHORT" in _signal_direction(r))
    avg_q = sum(_signal_quality(r) for r in rows) / max(1, len(rows))
    avg_p = sum(_signal_probability(r) for r in rows) / max(1, len(rows))
    directional = (long_n - short_n) / max(1, len(rows))
    mood = max(0, min(100, 50 + directional * 25 + (avg_q - 70) * 0.8 + (avg_p - 65) * 0.5))
    label = "Strong Bull" if mood >= 75 else "Bull" if mood >= 60 else "Neutral" if mood >= 40 else "Bear" if mood >= 25 else "Strong Bear"
    icon = "🟢" if mood >= 60 else "🟡" if mood >= 40 else "🔴"
    return (
        "🌡 <b>MARKET MOOD</b>\n\n"
        f"{icon} Индекс: <b>{mood:.0f}/100</b> — <b>{label}</b>\n"
        f"LONG / SHORT: <b>{long_n} / {short_n}</b>\n"
        f"Средний Quality: <b>{avg_q:.1f}</b>\n"
        f"Средняя Probability: <b>{avg_p:.1f}%</b>\n\n"
        "Индекс — внутренняя сводка по последним сигналам, а не отдельный прогноз цены."
    )


def build_heat_map_text():
    rows = get_recent_signals(limit=50)
    if not rows:
        return "🗺 <b>HEAT MAP</b>\n\nНедостаточно данных."
    latest = {}
    for row in rows:
        sym = _signal_symbol(row)
        if sym not in latest:
            latest[sym] = row
        if len(latest) >= 12:
            break
    lines = ["🗺 <b>HEAT MAP ПО ПОСЛЕДНИМ СИГНАЛАМ</b>", ""]
    for sym, row in latest.items():
        d = _signal_direction(row)
        arrow = "🟢 ↑" if "LONG" in d else "🔴 ↓" if "SHORT" in d else "🟡 →"
        lines.append(f"{arrow} <b>{html.escape(sym)}</b> • Q {_signal_quality(row):.0f} • P {_signal_probability(row):.0f}%")
    return "\n".join(lines)


def build_best_combos_text():
    rows = get_recent_signals(limit=100)
    counts = {}
    for row in rows:
        p = _signal_payload(row)
        hits = p.get("positiveProfileHits") or p.get("positive_profile_hits") or []
        if isinstance(hits, str): hits = [hits]
        for hit in hits:
            name = str(hit)
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "🧩 <b>ЛУЧШИЕ КОМБИНАЦИИ</b>\n\nВ последних сигналах нет сохранённых profile hits."
    top = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:8]
    lines = ["🧩 <b>ЧАЩЕ ВСЕГО СРАБАТЫВАЮЩИЕ ПРОФИЛИ</b>", ""]
    lines.extend(f"• <b>{html.escape(name)}</b>: {count}" for name, count in top)
    lines.append("\nДля оценки прибыльности используй статистику после накопления закрытых paper-сделок.")
    return "\n".join(lines)


def build_scanner_intelligence_text():
    from scanner_intelligence import get_last_scan_intelligence, aggregate_24h, is_previous_process_snapshot
    from trade_engine import is_trade_scan_running, get_trade_scan_runtime_state
    row = get_last_scan_intelligence()
    running = is_trade_scan_running()
    runtime = get_trade_scan_runtime_state()
    if not row:
        if running:
            return (
                "🔎 <b>SCANNER INTELLIGENCE</b>\n\n"
                "🟡 <b>Инициализация после запуска</b>\n"
                "🌍 Загружаю Multi-Exchange Universe\n"
                "🏦 Проверяю доступные биржи\n"
                "⏳ Первый полный скан ещё не завершён."
            )
        return "🔎 <b>SCANNER INTELLIGENCE</b>\n\nДанных ещё нет. Запусти торговый скан."
    st = row.get("stages") or {}
    previous_process = is_previous_process_snapshot(row)
    lines = [
        "🔎 <b>SCANNER INTELLIGENCE</b>", "",
    ]
    if running:
        owner = runtime.get('owner') or 'unknown'
        owner_text = {'monitor':'фоновый монитор','manual':'ручной скан','near-watch':'Near-Signal re-scan'}.get(owner, owner)
        processed = int(runtime.get('processed') or 0); total = int(runtime.get('total') or 0)
        phase = runtime.get('phase') or 'scan'
        lines += [
            "🟡 <b>Скан выполняется сейчас</b>",
            f"Источник: <b>{html.escape(str(owner_text))}</b> · этап: <b>{html.escape(str(phase))}</b>",
            f"Прогресс: <b>{processed}/{total}</b>" if total else "Прогресс: формируется Universe",
            "Ниже показан последний завершённый проход.", "",
        ]
    if previous_process:
        lines += [
            "☁️ <b>Восстановлено после redeploy из Supabase</b>",
            "Ниже — последний успешно завершённый скан предыдущего процесса.", "",
        ]
    lines += [
        f"Последний успешный скан: <code>{html.escape(str(row.get('runTimeUtc') or '—'))}</code>",
        f"Проверено: <b>{int(st.get('analyzed') or 0)}</b>", "",
        "<b>Воронка:</b>",
        f"• структура/status → <b>{int(st.get('status') or 0)}</b>",
        f"• Score → <b>{int(st.get('score') or 0)}</b>",
        f"• R/R → <b>{int(st.get('rr') or 0)}</b>",
        f"• Probability → <b>{int(st.get('probability') or 0)}</b>",
        f"• Quality → <b>{int(st.get('quality') or 0)}</b>",
        f"• EV → <b>{int(st.get('ev') or 0)}</b>",
        f"• ✅ сигналы → <b>{int(st.get('signals') or 0)}</b>",
    ]
    misses = row.get("nearMisses") or []
    if misses:
        lines += ["", "<b>Ближе всех:</b>"]
        for item in misses[:5]:
            lines.append(
                f"• <b>{html.escape(str(item.get('symbol') or '?'))}</b> — {html.escape(str(item.get('reason') or 'filter'))} "
                f"| P {item.get('probability') or '—'}% | Q {item.get('qualityScore') or '—'} | EV {item.get('expectedValuePct') or '—'}"
            )
    market_state = row.get("marketState") or {}
    if market_state:
        lines += ["", "<b>Состояние анализируемого рынка:</b>",
                  f"📈 LONG bias: {int(market_state.get('LONG_BIAS') or 0)} · 📉 SHORT bias: {int(market_state.get('SHORT_BIAS') or 0)} · ➖ neutral: {int(market_state.get('NO_TRADE') or 0)}"]
    dist = row.get("distributions") or {}
    qbands = dist.get("quality") or {}
    if qbands:
        lines += ["", "<b>Quality кандидатов:</b> " + " · ".join(f"{k}:{v}" for k, v in qbands.items() if v)]
    pbands = dist.get("probability") or {}
    if pbands:
        lines += ["<b>Probability:</b> " + " · ".join(f"{k}:{v}" for k, v in pbands.items() if v)]
    evbands = dist.get("ev") or {}
    if evbands:
        lines += ["<b>EV:</b> " + " · ".join(f"{k}:{v}" for k, v in evbands.items() if v)]
    recommendation = row.get("recommendation")
    if recommendation:
        lines += ["", "🧠 <b>Комментарий:</b>", html.escape(str(recommendation))]
    agg = aggregate_24h()
    if agg.get("scans"):
        lines += ["", f"<b>За 24ч:</b> {agg['scans']} сканов · {agg['analyzed']} проверок · {agg['signals']} сигналов"]
        if agg.get('analyzed'):
            base = max(1, agg['analyzed'])
            lines.append(
                "Проход: "
                f"status {100*agg['status']/base:.0f}% → score {100*agg['score']/base:.0f}% → "
                f"P {100*agg['probability']/base:.0f}% → Q {100*agg['quality']/base:.0f}% → EV {100*agg['ev']/base:.0f}%"
            )
    return "\n".join(lines)


def build_universe_dashboard_text():
    from scanner_intelligence import get_last_scan_intelligence
    latest = get_last_scan_intelligence()
    u = dict(latest.get("universe") or {}) if latest else get_last_universe_summary()
    providers = latest.get("providerStats") or {} if latest else {}
    lines = ["🌍 <b>MULTI-EXCHANGE UNIVERSE</b>", ""]
    if not u:
        return "\n".join(lines + ["Universe ещё не собран. Запусти торговый скан."])
    lines += [
        f"Бирж настроено: <b>{int(u.get('providersConfigured') or 0)}</b>",
        f"Бирж ответило: <b>{int(u.get('providersOk') or 0)}</b>",
        f"Контрактов просмотрено: <b>{int(u.get('contractsObserved') or 0)}</b>",
        f"Уникальных ликвидных символов: <b>{int(u.get('uniqueLiquidSymbols') or 0)}</b>",
        f"После coverage-фильтра: <b>{int(u.get('coverageEligibleSymbols') or 0)}</b>",
        f"⚡ Fast pool: <b>{int(u.get('fastPoolSymbols') or 0)}</b>",
        f"🧠 Deep scan за проход: <b>{int(u.get('selectedSymbols') or 0)}</b>",
        f"Минимум бирж на символ: <b>{int(u.get('minVenues') or 1)}</b>",
    ]
    buckets = u.get('selectionBuckets') or {}
    if buckets:
        labels = {'liquidity':'ликвидность','gainer':'рост','loser':'падение','coverage':'coverage','mover':'движение','liquidity_fill':'ликвидность+'}
        lines += ['', '<b>Состав Deep Scan:</b> ' + ' · '.join(f"{labels.get(k,k)} {v}" for k,v in buckets.items())]
    if providers:
        lines += ["", "<b>По биржам:</b>"]
        for name, info in providers.items():
            icon = "🟢" if info.get("ok") else "🔴"
            lines.append(
                f"{icon} {html.escape(str(name).upper())}: contracts {int(info.get('tradable') or 0)} · liquid {int(info.get('eligible') or 0)}"
            )
    lines += ["", "Coverage повышает приоритет монет, доступных сразу на нескольких биржах; сам по себе он не ослабляет Quality/EV фильтры."]
    return "\n".join(lines)


def build_near_signal_text():
    from near_signal_watchlist import get_rows
    rows = get_rows(limit=12)
    lines = [
        '🟡 <b>NEAR-SIGNAL WATCHLIST</b>', '',
        'Только кандидаты, которым не хватает <b>одного</b> фильтра и которые действительно близки к его порогу.', ''
    ]
    if not rows:
        return '\n'.join(lines + ['Сейчас реальных near-signal кандидатов нет.'])

    def _metric(value, kind):
        if value is None:
            return '—'
        try:
            value = float(value)
        except Exception:
            return '—'
        if kind == 'Probability': return f'{value:.1f}%'
        if kind in ('Quality', 'Score'): return f'{value:.1f}'
        if kind == 'EV': return f'{value:.2f}%'
        if kind == 'R/R': return f'{value:.2f}'
        return f'{value:.2f}'

    for index, row in enumerate(rows, 1):
        gate = str(row.get('missing_gate') or row.get('reason') or 'filter')
        current = row.get('current_value')
        threshold = row.get('threshold_value')
        distance = float(row.get('distance_score') or 0)
        q = row.get('quality')
        ev = row.get('ev')
        pval = row.get('probability')
        q_text = '—' if q is None else f'{float(q):.1f}'
        ev_text = '—' if ev is None else f'{float(ev):.2f}'
        lines.extend([
            f"<b>{index}. {html.escape(str(row.get('symbol') or '?'))}</b> — близость <b>{distance:.0f}%</b>",
            f"└ Не хватает: <b>{html.escape(gate)}</b> {_metric(current, gate)} → {_metric(threshold, gate)}",
            f"   P {_metric(pval, 'Probability')} · Q {q_text} · EV {ev_text}",
            ''
        ])
    lines.append('🔄 Чем выше близость, тем раньше кандидат попадает в повторный scan.')
    return '\n'.join(lines).rstrip()


def build_shadow_signals_text():
    from shadow_signals import summary
    st = summary(); counts = st.get('counts') or {}
    return (
        '👻 <b>SHADOW SIGNALS</b>\n\n'
        'Не отправляются как сделки и не влияют на Paper PnL. Нужны, чтобы понять, какие фильтры отсекают потенциально прибыльные идеи.\n\n'
        f"Ждут вход: <b>{int(counts.get('pending_entry') or 0)}</b>\n"
        f"Вход подтверждён: <b>{int(counts.get('filled') or 0)}</b>\n"
        f"Не состоялись: <b>{int(counts.get('expired') or 0)}</b>\n"
        f"Наблюдение завершено: <b>{int(counts.get('observed') or 0)}</b>\n\n"
        f"24h выборка: <b>{int(st.get('outcomes24h') or 0)}</b> · WR <b>{float(st.get('winRate24h') or 0):.1f}%</b> · Avg <b>{float(st.get('avgReturn24h') or 0):+.2f}%</b>"
    )


def _format_provider_time(ts):
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%H:%M:%S UTC")
    except Exception:
        return "—"


def build_exchange_status_text(active_probe=True):
    probe_rows = probe_provider_health() if active_probe else []
    probe_by_name = {row.get("provider"): row for row in probe_rows}
    rows = get_provider_health_snapshot()
    configured = [row.get("provider") for row in rows]
    lines = [
        "🏦 <b>БИРЖИ И ИСТОЧНИКИ РЫНКА</b>",
        "",
        "Universe собирается сразу с нескольких публичных futures API; данные по каждой монете берутся через fallback-цепочку.",
        f"Порядок: <b>{' → '.join(name.upper() for name in configured)}</b>",
        "",
    ]
    online = 0
    for row in rows:
        name = str(row.get("provider") or "?").upper()
        probe = probe_by_name.get(row.get("provider"))
        if probe is not None:
            ok = bool(probe.get("ok"))
            status = "online" if ok else "degraded"
            latency = probe.get("latency_ms")
        else:
            status = row.get("status") or "unknown"
            latency = None
        if status == "online":
            icon, label = "🟢", "ONLINE"
            online += 1
        elif status == "cooldown":
            icon, label = "🟠", f"COOLDOWN {int(row.get('cooldown_remaining') or 0)}s"
        elif status == "degraded":
            icon, label = "🔴", "DEGRADED"
        else:
            icon, label = "⚪", "НЕ ПРОВЕРЕНА"
        lines.append(f"{icon} <b>{name}</b> — <b>{label}</b>")
        if latency is not None:
            lines.append(f"   ↳ ping: <b>{int(latency)} ms</b>")
        if row.get("last_success_at"):
            lines.append(f"   ↳ последний успех: {_format_provider_time(row.get('last_success_at'))}")
        if int(row.get("tradable_symbols") or 0):
            eligible = int(row.get("eligible_symbols") or 0)
            lines.append(f"   ↳ USDT perpetual: <b>{int(row.get('tradable_symbols') or 0)}</b> · ликвидных: <b>{eligible}</b>")
        if row.get("error") and status != "online":
            err = html.escape(str(row.get("error"))[:180])
            lines.append(f"   ↳ ошибка: <code>{err}</code>")
        lines.append("")
    lines.append(f"Доступно сейчас: <b>{online}/{len(rows)}</b>")
    lines.append("")
    lines.append("Если первая биржа недоступна или не знает конкретный символ, клиент переключается на следующую.")
    return "\n".join(lines)


def build_paper_goal_text():
    st = _paper_snapshot()
    closed = st["closed"]
    goal = 50
    ratio = max(0, min(1, closed / goal))
    filled = int(round(ratio * 10))
    bar = "█" * filled + "░" * (10 - filled)
    roi = ((st["balance"] / st["initial"] - 1) * 100) if st["initial"] else 0
    return (
        "🏁 <b>ТЕСТ СТРАТЕГИИ: 50 PAPER-СДЕЛОК</b>\n\n"
        f"<code>{bar}</code> <b>{closed}/{goal}</b>\n"
        f"Старт: <b>${st['initial']:.2f}</b>\n"
        f"Баланс: <b>${st['balance']:.2f}</b>\n"
        f"PnL: <b>{st['pnl']:+.2f} USDT</b>\n"
        f"ROI: <b>{roi:+.2f}%</b>\n"
        f"Win rate: <b>{st['win_rate']:.1f}%</b>\n"
        f"Profit Factor: <b>{st['pf']:.2f}</b>\n\n"
        "До завершения теста параметры стратегии лучше не менять."
    )


def build_paper_status_text():
    stats = get_paper_performance()
    account = stats.get("account") or {}
    free_balance = float(stats.get("derived_free_balance") if stats.get("derived_free_balance") is not None else account.get("balance") or 0)
    equity = float(stats.get("derived_equity") if stats.get("derived_equity") is not None else account.get("equity") or 0)
    initial = float(account.get("initial_balance") or 0)
    pnl = float(stats.get("net_pnl") or 0)
    enabled = boolean("PAPER_TRADING_ENABLED", True)
    pf = float(stats.get("profit_factor") or 0)
    pf_text = "∞" if pf >= 999 else f"{pf:.2f}"
    drift = max(abs(float(stats.get("accounting_drift_balance") or 0)), abs(float(stats.get("accounting_drift_equity") or 0)))
    sync_line = "🟢 учёт согласован" if drift < 0.01 else f"🟠 расхождение ledger/account: ${drift:.4f}"
    return (
        "🧪 <b>PAPER TRADING</b>\n\n"
        f"Статус: <b>{'🟢 включён' if enabled else '⚪ выключен'}</b>\n"
        f"Стартовый капитал: <b>${initial:.2f}</b>\n"
        f"Свободный баланс: <b>${free_balance:.2f}</b>\n"
        f"Equity (с учётом открытых): <b>${equity:.2f}</b>\n"
        f"Net PnL: <b>{pnl:+.4f} USDT</b> • ROI <b>{float(stats.get('roi_pct') or 0):+.2f}%</b>\n\n"
        f"Закрыто: <b>{stats.get('closed_count', 0)}</b> • ✅ {stats.get('wins',0)} / ❌ {stats.get('losses',0)} / ➖ {stats.get('breakeven',0)}\n"
        f"Win Rate: <b>{float(stats.get('win_rate') or 0):.2f}%</b> • PF <b>{pf_text}</b>\n"
        f"TP: <b>{stats.get('tp_closes',0)}</b> • SL: <b>{stats.get('sl_closes',0)}</b> • 💥 Liquidation: <b>{stats.get('liquidations',0)}</b> • Time: <b>{stats.get('time_exits',0)}</b>\n"
        f"Открытых: <b>{len(stats.get('open_positions') or [])}</b> • ждут entry: <b>{len(stats.get('pending_positions') or [])}</b>\n"
        f"Учёт: <b>{sync_line}</b>\n\n"
        "Liquidation отслеживается по 1m/5m OHLC с перекрытием окна; если свеча достигает liquidation level, Paper использует консервативный liquidation outcome."
    )


def build_paper_positions_text():
    rows = get_paper_positions()
    if not rows:
        return "📂 <b>ОТКРЫТЫЕ PAPER-ПОЗИЦИИ</b>\n\nОткрытых позиций нет."
    lines = ["📂 <b>ОТКРЫТЫЕ PAPER-ПОЗИЦИИ</b>", ""]
    for row in rows[:20]:
        lines.extend([
            f"<b>{html.escape(str(row.get('symbol')))}</b> {row.get('side')} • {int(row.get('leverage') or 1)}x",
            f"Margin: ${float(row.get('margin_usd') or 0):.2f} • Notional: ${float(row.get('notional_usd') or 0):.2f}",
            f"Entry <code>{float(row.get('entry_price') or 0):.8g}</code> • TP <code>{float(row.get('tp1_price') or 0):.8g}</code>",
            f"SL <code>{float(row.get('stop_price') or 0):.8g}</code> • Liq <code>{float(row.get('estimated_liquidation_price') or 0):.8g}</code>",
            "",
        ])
    return "\n".join(lines).rstrip()


def build_paper_history_text():
    rows = get_paper_trades(20)
    if not rows:
        return "📜 <b>ИСТОРИЯ PAPER-СДЕЛОК</b>\n\nЗакрытых сделок пока нет."
    lines = ["📜 <b>ИСТОРИЯ PAPER-СДЕЛОК</b>", ""]
    for row in rows:
        pnl = float(row.get("net_pnl") or 0)
        reason = str(row.get("close_reason") or "")
        icon = "💥" if reason.startswith("LIQUIDATION") else ("✅" if pnl > 0 else ("➖" if abs(pnl) <= 1e-9 else "❌"))
        lines.append(
            f"{icon} <b>{html.escape(str(row.get('symbol')))}</b> {row.get('side')} • "
            f"{row.get('close_reason')} • <b>{pnl:+.4f} USDT</b> "
            f"({float(row.get('return_on_margin_pct') or 0):+.2f}%)"
        )
    return "\n".join(lines)


def _paper_trade_dt(row):
    value = row.get("closed_at") or row.get("created_at")
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _trade_metrics(rows, initial_balance=100.0):
    rows = list(rows or [])
    pnls = [float(r.get("net_pnl") or 0) for r in rows]
    eps = 1e-9
    wins = [x for x in pnls if x > eps]
    losses = [x for x in pnls if x < -eps]
    breakeven = [x for x in pnls if abs(x) <= eps]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)
    pf = gross_profit / gross_loss if gross_loss > 1e-12 else (999.0 if gross_profit > 0 else 0.0)
    resolved_directional = len(wins) + len(losses)
    win_rate = len(wins) / resolved_directional * 100 if resolved_directional else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    fees = sum(float(r.get("fees") or 0) for r in rows)

    equity = float(initial_balance)
    peak = equity
    max_dd = 0.0
    max_dd_pct = 0.0
    best_win_streak = 0
    worst_loss_streak = 0
    win_streak = 0
    loss_streak = 0
    ordered = sorted(rows, key=lambda r: _paper_trade_dt(r) or datetime.min.replace(tzinfo=timezone.utc))
    for row in ordered:
        pnl = float(row.get("net_pnl") or 0)
        equity += pnl
        peak = max(peak, equity)
        dd = peak - equity
        dd_pct = dd / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)
        if pnl > eps:
            win_streak += 1
            loss_streak = 0
            best_win_streak = max(best_win_streak, win_streak)
        elif pnl < -eps:
            loss_streak += 1
            win_streak = 0
            worst_loss_streak = max(worst_loss_streak, loss_streak)
        else:
            # Breakeven is neither a win nor a loss and must not inflate a loss streak.
            win_streak = 0
            loss_streak = 0
    return {
        "count": len(rows), "wins": len(wins), "losses": len(losses), "breakeven": len(breakeven),
        "net": net, "win_rate": win_rate, "pf": pf,
        "avg_win": avg_win, "avg_loss": avg_loss, "fees": fees,
        "roi": net / initial_balance * 100 if initial_balance else 0.0,
        "max_dd": max_dd, "max_dd_pct": max_dd_pct,
        "best_win_streak": best_win_streak, "worst_loss_streak": worst_loss_streak,
    }


def build_performance_center_text():
    stats = get_paper_performance()
    account = stats.get("account") or {}
    rows = stats.get("trades") or []
    initial = float(account.get("initial_balance") or 100.0)
    m = _trade_metrics(rows, initial)
    realized_balance = float(stats.get("realized_equity") if stats.get("realized_equity") is not None else initial + m["net"])
    open_count = len(stats.get("open_positions") or [])
    pf_text = "∞" if m["pf"] >= 999 else f"{m['pf']:.2f}"
    return (
        "📈 <b>РЕЗУЛЬТАТЫ СТРАТЕГИИ</b>\n\n"
        f"💰 Реализованный капитал: <b>${realized_balance:.2f}</b>\n"
        f"PnL: <b>{m['net']:+.2f} USDT</b> • ROI <b>{m['roi']:+.2f}%</b>\n"
        f"🎯 Сделок: <b>{m['count']}</b> • открыто <b>{open_count}</b>\n"
        f"✅ {m['wins']} / ❌ {m['losses']} / ➖ {m['breakeven']} • Win Rate <b>{m['win_rate']:.1f}%</b> • PF <b>{pf_text}</b>\n"
        f"📉 Max DD: <b>-${m['max_dd']:.2f}</b> ({m['max_dd_pct']:.1f}%)\n"
        f"💸 Комиссии: <b>${m['fees']:.2f}</b>\n\n"
        f"Средняя прибыль: <b>{m['avg_win']:+.2f}$</b> • средний убыток: <b>{m['avg_loss']:+.2f}$</b>\n"
        f"Серии: 🟢 <b>{m['best_win_streak']}</b> / 🔴 <b>{m['worst_loss_streak']}</b>\n\n"
        "Ниже — только самые полезные разрезы для оценки текущего сетапа."
    )


def _period_rows(days=None, today=False):
    rows = get_paper_trades(1000)
    now = datetime.now(timezone.utc)
    out = []
    for row in rows:
        dt = _paper_trade_dt(row)
        if dt is None:
            continue
        if today and dt.date() != now.date():
            continue
        if days is not None and dt < now - timedelta(days=days):
            continue
        out.append(row)
    return out


def build_period_performance_text(title, rows):
    account = get_paper_performance().get("account") or {}
    initial = float(account.get("initial_balance") or 100.0)
    m = _trade_metrics(rows, initial)
    if not rows:
        return f"{title}\n\nЗакрытых сделок за этот период пока нет."
    best = max(rows, key=lambda r: float(r.get("net_pnl") or 0))
    worst = min(rows, key=lambda r: float(r.get("net_pnl") or 0))
    pf_text = "∞" if m["pf"] >= 999 else f"{m['pf']:.2f}"
    return (
        f"{title}\n\n"
        f"Сделок: <b>{m['count']}</b> • Win Rate <b>{m['win_rate']:.1f}%</b>\n"
        f"PnL: <b>{m['net']:+.2f}$</b> • PF <b>{pf_text}</b>\n"
        f"Комиссии: <b>${m['fees']:.2f}</b>\n\n"
        f"🏆 {html.escape(str(best.get('symbol') or '?'))}: <b>{float(best.get('net_pnl') or 0):+.2f}$</b>\n"
        f"🔻 {html.escape(str(worst.get('symbol') or '?'))}: <b>{float(worst.get('net_pnl') or 0):+.2f}$</b>"
    )


def build_coin_performance_text():
    rows = get_paper_trades(1000)
    if not rows:
        return "🏆 <b>РЕЗУЛЬТАТЫ ПО МОНЕТАМ</b>\n\nЗакрытых сделок пока нет."
    grouped = {}
    for row in rows:
        sym = str(row.get("symbol") or "?").upper()
        grouped.setdefault(sym, []).append(row)
    ranking = []
    for sym, trades in grouped.items():
        m = _trade_metrics(trades, 100.0)
        ranking.append((m["net"], sym, m))
    ranking.sort(reverse=True)
    lines = ["🏆 <b>РЕЗУЛЬТАТЫ ПО МОНЕТАМ</b>", "", "🟢 <b>Лучшие</b>"]
    for net, sym, m in ranking[:5]:
        lines.append(f"• <b>{html.escape(sym)}</b>: {net:+.2f}$ • {m['count']} сделок • WR {m['win_rate']:.0f}%")
    losers = sorted(ranking, key=lambda x: x[0])[:5]
    if losers and losers[0][0] < 0:
        lines.extend(["", "🔴 <b>Худшие</b>"])
        for net, sym, m in losers:
            if net >= 0:
                continue
            lines.append(f"• <b>{html.escape(sym)}</b>: {net:+.2f}$ • {m['count']} сделок • WR {m['win_rate']:.0f}%")
    return "\n".join(lines)


def _band_summary(rows, key, bands):
    result = []
    for label, low, high in bands:
        selected = []
        for row in rows:
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                continue
            if value >= low and (high is None or value < high):
                selected.append(row)
        if selected:
            m = _trade_metrics(selected, 100.0)
            pf = "∞" if m["pf"] >= 999 else f"{m['pf']:.2f}"
            result.append(f"• <b>{label}</b>: {m['count']} • WR {m['win_rate']:.0f}% • PnL {m['net']:+.2f}$ • PF {pf}")
    return result


def build_filter_performance_text():
    rows = get_paper_trades(1000)
    if not rows:
        return "🎚 <b>ЭФФЕКТИВНОСТЬ ФИЛЬТРОВ</b>\n\nЗакрытых сделок пока нет."
    lines = ["🎚 <b>ЭФФЕКТИВНОСТЬ ФИЛЬТРОВ</b>", "", "<b>Quality</b>"]
    lines += _band_summary(rows, "quality_score", [("85+",85,None),("80–85",80,85),("75–80",75,80),("<75",-1e9,75)])
    lines += ["", "<b>Probability</b>"]
    lines += _band_summary(rows, "probability", [("80%+",80,None),("75–80%",75,80),("70–75%",70,75),("<70%",-1e9,70)])
    lines += ["", "<b>Expected Value</b>"]
    lines += _band_summary(rows, "expected_value_pct", [("5%+",5,None),("3–5%",3,5),("2–3%",2,3),("<2%",-1e9,2)])
    lines.append("\nПорог стоит менять только после достаточной выборки; сейчас цель — 50 закрытых paper-сделок.")
    return "\n".join(lines)

def fmt_optional(value, digits=2, suffix=""):
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except Exception:
        return str(value)

def format_event(row):
    return f"{row.get('at','')} · {row.get('event','UNKNOWN')}"
