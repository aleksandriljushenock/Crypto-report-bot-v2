"""Resilient multi-source Smart Money Engine."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Mapping

from core.logging_setup import get_logger
from smart_money_sources import COLLECTORS, safe_collect
from v8_store import save_snapshot

logger = get_logger(__name__)

WEIGHTS = {
    "whale_alert": 0.18,
    "exchange_netflow": 0.18,
    "etf_flow": 0.14,
    "stablecoin_flow": 0.12,
    "funding": 0.10,
    "open_interest": 0.14,
    "liquidations": 0.14,
}
LABELS = {
    "whale_alert": "Whale Activity",
    "exchange_netflow": "Exchange Netflow",
    "etf_flow": "ETF Flow",
    "stablecoin_flow": "Stablecoin Flow",
    "funding": "Funding",
    "open_interest": "Open Interest",
    "liquidations": "Liquidations",
}


def _num(value: Any, default: float = 50.0) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return default


def calculate_smart_money_score(components: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate score from plain scores or rich source result dictionaries."""
    scores: dict[str, float] = {}
    qualities: dict[str, str] = {}
    for key in WEIGHTS:
        raw = components.get(key)
        if isinstance(raw, Mapping):
            if raw.get("available") and raw.get("score") is not None:
                scores[key] = _num(raw.get("score"))
                qualities[key] = str(raw.get("quality") or "direct")
        elif raw is not None:
            scores[key] = _num(raw)
            qualities[key] = "direct"

    if not scores:
        score, coverage, direct_coverage = 50.0, 0.0, 0.0
    else:
        denominator = sum(WEIGHTS[k] for k in scores)
        score = sum(scores[k] * WEIGHTS[k] for k in scores) / denominator
        coverage = denominator / sum(WEIGHTS.values()) * 100.0
        direct_weight = sum(WEIGHTS[k] for k in scores if qualities.get(k) == "direct")
        direct_coverage = direct_weight / sum(WEIGHTS.values()) * 100.0

    bias = "ACCUMULATION" if score >= 62 else ("DISTRIBUTION" if score <= 38 else "NEUTRAL")
    confidence = min(95.0, coverage * 0.7 + direct_coverage * 0.3)
    return {
        "smart_money_score": round(score, 2),
        "bias": bias,
        "coverage": round(coverage, 1),
        "direct_coverage": round(direct_coverage, 1),
        "confidence": round(confidence, 1),
        "components": {k: scores.get(k) for k in WEIGHTS},
    }


def collect_smart_money(symbol: str = "BTCUSDT") -> dict[str, Any]:
    symbol = (symbol or "BTCUSDT").upper().replace("/", "")
    workers = max(1, min(int(os.getenv("SMART_MONEY_MAX_WORKERS", "7")), len(COLLECTORS)))
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="smart-money") as pool:
        futures = {pool.submit(safe_collect, name, symbol): name for name in COLLECTORS}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:  # defensive: safe_collect should already contain failures
                logger.exception("Unexpected Smart Money collector failure: %s", name)
                results[name] = {"component": name, "available": False, "score": None, "error": str(exc), "quality": "optional"}

    aggregate = calculate_smart_money_score(results)
    payload = {
        "symbol": symbol,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        **aggregate,
        "sources": results,
    }
    save_snapshot("smart_money", payload, symbol=symbol, score=aggregate["smart_money_score"])
    return payload


def scan_smart_money(symbol: str = "BTCUSDT") -> list[dict[str, Any]]:
    """Backward-compatible event-like output for older callers."""
    snapshot = collect_smart_money(symbol)
    events = []
    for key, item in snapshot["sources"].items():
        if item.get("available"):
            events.append({
                "title": f"{LABELS[key]}: {item.get('value')} {item.get('unit', '')}".strip(),
                "source": item.get("source", key),
                "score": item.get("score", 50),
                "quality": item.get("quality"),
                "detectedAt": item.get("observed_at"),
            })
    return sorted(events, key=lambda row: abs(float(row.get("score", 50)) - 50), reverse=True)


def build_smart_money_report(symbol: str = "BTCUSDT") -> str:
    data = collect_smart_money(symbol)
    lines = [
        "<b>🐋 SMART MONEY ENGINE 2.1</b>",
        f"Актив: <b>{data['symbol']}</b>",
        "",
        f"Smart Money Score: <b>{data['smart_money_score']}</b>/100",
        f"Режим: <b>{data['bias']}</b>",
        f"Покрытие компонентов: <b>{data['coverage']}%</b>",
        f"Прямые источники: <b>{data['direct_coverage']}%</b>",
        f"Уверенность: <b>{data['confidence']}%</b>",
        "",
    ]
    for key in WEIGHTS:
        item = data["sources"].get(key, {})
        label = LABELS[key]
        if item.get("available"):
            quality = "прямой" if item.get("quality") == "direct" else "proxy"
            value = item.get("value")
            unit = item.get("unit") or ""
            lines.append(f"✅ <b>{label}</b>: {item.get('score')}/100 · {value} {unit} · {quality}")
            lines.append(f"   <i>{item.get('source')}</i>")
        else:
            error = (item.get("error") or item.get("note") or "источник недоступен")[:180]
            lines.append(f"⚠️ <b>{label}</b>: нет данных")
            lines.append(f"   <code>{error}</code>")
    lines += ["", "<i>Proxy-компоненты поддерживают работу без платных on-chain API и явно помечаются; они не выдаются за wallet-labelled netflow/ликвидации.</i>"]
    return "\n".join(lines)
