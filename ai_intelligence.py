"""High-level v12 intelligence scan, ranking, reports and alerts."""
from __future__ import annotations

import html
import os
from typing import Any, Dict

from ai_score_engine import enrich_signal, get_score_history, get_top_scores, save_ai_score


def rank_signals(signals):
    enriched = []
    for signal in signals or []:
        item = enrich_signal(signal)
        save_ai_score(item)
        enriched.append(item)
    enriched.sort(key=lambda x: (float(x.get("aiScore") or 0), float(x.get("rr") or 0)), reverse=True)
    return enriched


def run_ai_intelligence(max_results: int = 15) -> Dict[str, Any]:
    from trade_engine import run_trade_scan
    result = run_trade_scan(include_watch=True, max_results=max_results, apply_ai=False)
    ranked = rank_signals(result.get("signals", []))
    result["signals"] = ranked
    result["aiVersion"] = "12.0"
    return result


def _bar(score: float) -> str:
    filled = max(0, min(10, round(float(score or 0) / 10)))
    return "█" * filled + "░" * (10 - filled)


def build_top_ai_report(limit: int = 10, refresh: bool = True) -> str:
    if refresh:
        try:
            run_ai_intelligence(max_results=max(limit, 12))
        except Exception:
            pass
    rows = get_top_scores(limit=limit, hours=int(os.getenv("AI_TOP_WINDOW_HOURS", "48")))
    lines = ["<b>🏆 TOP AI — Learning MAX v14</b>", ""]
    if not rows:
        return "\n".join(lines + ["Пока нет AI-оценок. Запусти поиск торговых входов."])
    for index, row in enumerate(rows, 1):
        symbol = html.escape(str(row.get("symbol") or "N/A"))
        score = float(row.get("ai_score") or 0)
        tier = html.escape(str(row.get("tier") or ""))
        direction = html.escape(str(row.get("direction") or "N/A"))
        lines.extend([
            f"<b>{index}. {symbol}</b> — <b>{score:.1f}/100</b> ({tier})",
            f"<code>{_bar(score)}</code> · {direction}",
            "",
        ])
    return "\n".join(lines).rstrip()


def build_signal_ai_block(signal: Dict[str, Any]) -> str:
    item = signal if "aiScore" in signal else enrich_signal(signal)
    labels = {
        "trend": "Trend", "momentum": "Momentum", "volume": "Volume",
        "funding": "Funding", "open_interest": "Open Interest", "alignment": "TF Alignment",
        "risk_reward": "Risk/Reward", "capital_flow": "Capital Flow",
        "narrative": "Narrative", "news": "News", "smart_money": "Smart Money",
    }
    factors = item.get("aiFactors") or {}
    best = sorted(factors.items(), key=lambda x: x[1], reverse=True)[:5]
    lines = [
        f"<b>🧠 AI SCORE: {item.get('aiScore', 0)}/100 — {item.get('aiTier', '')}</b>",
        f"<code>{_bar(item.get('aiScore', 0))}</code>",
        f"Вероятность: <b>{item.get('aiProbability', 0)}%</b> · неопределённость ±{item.get('aiUncertainty', 0)}%",
        f"Режим: <code>{item.get('aiRegime', 'range')}</code>", "",
        "<b>Главные факторы:</b>",
    ]
    for key, value in best:
        lines.append(f"• {labels.get(key, key)}: <b>{value}</b>")
    penalty = float(item.get("aiNoTradePenalty") or 0)
    if penalty > 0:
        lines += ["", f"⚠️ No-Trade penalty: <b>−{penalty:.1f}</b>"]
        for rule in (item.get("aiNoTradeRules") or [])[:3]:
            lines.append(f"• {labels.get(rule.get('feature'), rule.get('feature'))} {rule.get('operator')} {rule.get('threshold')}")
    adj = float(item.get("aiLearningAdjustment") or 0)
    if adj:
        lines += ["", f"Learning adjustment: <b>{adj:+.1f}</b>"]
    lines += ["", f"Model: <code>{item.get('aiVersion', 'base')}</code>"]
    return "\n".join(lines)


def build_ai_history_report(symbol: str = "", limit: int = 12) -> str:
    symbol = symbol.upper().strip()
    if not symbol:
        rows = get_top_scores(limit=5, hours=168)
        lines = ["<b>📚 ИСТОРИЯ AI SCORE</b>", "", "Укажи монету: <code>/aihistory BTCUSDT</code>", ""]
        if rows:
            lines.append("Последние лидеры: " + ", ".join(str(x.get("symbol")) for x in rows))
        return "\n".join(lines)
    rows = get_score_history(symbol, limit=limit)
    lines = [f"<b>📚 AI HISTORY — {html.escape(symbol)}</b>", ""]
    if not rows:
        return "\n".join(lines + ["История пока отсутствует."])
    for row in rows:
        created = str(row.get("created_at") or "").replace("T", " ")[:16]
        lines.append(f"{created} · <b>{float(row.get('ai_score') or 0):.1f}</b> · {row.get('tier')}")
    return "\n".join(lines)
