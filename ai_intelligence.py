"""High-level v12 intelligence scan, ranking, reports and alerts."""
from __future__ import annotations

import html
import os
from typing import Any, Dict

from ai_score_engine import enrich_signal, get_score_history, get_top_scores, save_ai_score

_LAST_RANK_DIAGNOSTICS = {}

def get_last_rank_diagnostics():
    return dict(_LAST_RANK_DIAGNOSTICS)


def rank_signals(signals):
    """Pre-rank cheaply, run Chronos only on finalists, then re-evaluate EV/quality."""
    global _LAST_RANK_DIAGNOSTICS
    prepared = []
    gate_enabled = os.getenv("HEDGE_QUALITY_GATE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    for signal in signals or []:
        item = enrich_signal(signal)
        try:
            from ai_hedge_fund_engine import evaluate_signal
            item.update(evaluate_signal(item))
        except Exception as exc:
            item["hedgeEngineFallback"] = str(exc)[:300]
            item.setdefault("qualityPassed", True)
            item.setdefault("qualityScore", float(item.get("aiScore") or 0))
            item.setdefault("expectedValuePct", 0.0)
        prepared.append(item)

    # First pass is deliberately Chronos-free and cheap.
    prepared.sort(
        key=lambda x: (
            float(x.get("expectedValuePct") or -999),
            float(x.get("qualityScore") or 0),
            float(x.get("aiScore") or 0),
        ),
        reverse=True,
    )

    try:
        from chronos_forecaster import apply_to_finalists
        prepared = apply_to_finalists(prepared)
    except Exception as exc:
        for item in prepared:
            item.pop("_chronosCloses", None)
            item.setdefault("chronosStatus", "error")
            item.setdefault("chronosError", str(exc)[:200])

    # Chronos changes probability/confidence, so calculate EV and gate once more.
    ranked = []
    rejected = []
    min_quality = float(os.getenv("HEDGE_MIN_QUALITY", "70"))
    min_ev = float(os.getenv("HEDGE_MIN_EV_PCT", "0.20"))
    quality_count = 0
    ev_count = 0
    for item in prepared:
        try:
            from ai_hedge_fund_engine import evaluate_signal
            item.update(evaluate_signal(item))
        except Exception:
            pass
        save_ai_score(item)
        quality = float(item.get("qualityScore") or 0)
        ev = float(item.get("expectedValuePct") or 0)
        if quality >= min_quality:
            quality_count += 1
        if quality >= min_quality and ev >= min_ev:
            ev_count += 1
        if not gate_enabled or item.get("qualityPassed"):
            ranked.append(item)
        else:
            reasons = []
            if quality < min_quality:
                reasons.append("Quality")
            if ev < min_ev:
                reasons.append("EV")
            if item.get("antiProfileHits"):
                reasons.append("anti-profile")
            rejected.append({
                "symbol": item.get("symbol"), "score": item.get("score"),
                "rr": item.get("rr"), "probability": item.get("probability"),
                "qualityScore": quality, "expectedValuePct": ev,
                "reason": ", ".join(reasons) or "AI gate",
            })
    quality_bands = {"85+": 0, "80-85": 0, "75-80": 0, "70-75": 0, "<70": 0}
    probability_bands = {"80+": 0, "75-80": 0, "70-75": 0, "<70": 0}
    ev_bands = {"5+": 0, "3-5": 0, "2-3": 0, "<2": 0}
    for item in prepared:
        q = float(item.get("qualityScore") or 0)
        pr = float(item.get("calibratedProbability") or item.get("probability") or 0)
        evv = float(item.get("expectedValuePct") or 0)
        quality_bands["85+" if q >= 85 else "80-85" if q >= 80 else "75-80" if q >= 75 else "70-75" if q >= 70 else "<70"] += 1
        probability_bands["80+" if pr >= 80 else "75-80" if pr >= 75 else "70-75" if pr >= 70 else "<70"] += 1
        ev_bands["5+" if evv >= 5 else "3-5" if evv >= 3 else "2-3" if evv >= 2 else "<2"] += 1
    _LAST_RANK_DIAGNOSTICS = {
        "input": len(signals or []), "evaluated": len(prepared),
        "quality": quality_count, "ev": ev_count,
        "passed": len(ranked),
        "rejected": sorted(rejected, key=lambda x: (float(x.get("qualityScore") or 0), float(x.get("expectedValuePct") or -999)), reverse=True)[:8],
        "qualityBands": quality_bands, "probabilityBands": probability_bands, "evBands": ev_bands,
    }
    ranked.sort(
        key=lambda x: (
            float(x.get("expectedValuePct") or -999),
            float(x.get("qualityScore") or 0),
            float(x.get("aiScore") or 0),
        ),
        reverse=True,
    )
    return ranked


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
