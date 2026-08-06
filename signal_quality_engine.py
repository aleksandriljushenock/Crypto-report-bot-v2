"""Profit-oriented, explainable signal quality gate.

The default LONG profile is based on the user's 1,144 resolved LONG signals.
Rules are deliberately conservative and configurable. They improve selection,
but do not guarantee profit. SHORT signals use only direction-neutral rules until
sufficient resolved SHORT history is available.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Tuple


def _f(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _env(name: str, default: float) -> float:
    return _f(os.getenv(name, default), default)


def _add(reasons: List[Dict[str, Any]], kind: str, name: str, points: float, detail: str) -> float:
    reasons.append({"kind": kind, "name": name, "points": round(points, 2), "detail": detail})
    return points


def _direction_target(direction: str) -> str:
    return "UP" if str(direction).upper() == "LONG_BIAS" else "DOWN"


def _chronos_agreement(signal: Dict[str, Any]) -> Tuple[bool, float]:
    chronos = signal.get("chronos") or signal.get("chronosForecast") or {}
    agreement = bool(chronos.get("directionAgreement"))
    strength = max(
        _f(chronos.get("probabilityUp")),
        _f(chronos.get("probabilityDown")),
        _f(chronos.get("strength")),
    )
    return agreement, strength


def evaluate_signal_quality(signal: Dict[str, Any]) -> Dict[str, Any]:
    direction = str(signal.get("direction") or "").upper()
    long_mode = direction == "LONG_BIAS"
    factors = signal.get("aiFactors") or {}
    timeframes = signal.get("timeframes") or {}
    setup = str(signal.get("setup") or "").upper()
    structure_1h = str(signal.get("structure1h") or "").upper()
    structure_15m = str(signal.get("structure15m") or "").upper()

    probability = _f(signal.get("probability", signal.get("aiProbability", 50)), 50)
    confidence = _f(signal.get("confidence", 50), 50)
    uncertainty = _f(signal.get("uncertainty", signal.get("aiUncertainty", 50)), 50)
    ai_score = _f(signal.get("aiScore", signal.get("score", 50)), 50)
    raw_score = _f(signal.get("score"), 50)
    rr = _f(signal.get("rr"), 0)
    quote_volume = _f(signal.get("quoteVolume"), 0)
    alignment = _f(signal.get("alignment", factors.get("alignment", 50)), 50)
    trend = _f(factors.get("trend", alignment), alignment)
    volume = _f(factors.get("volume"), 50)
    capital_flow = _f(factors.get("capital_flow"), 50)
    smart_money = _f(factors.get("smart_money"), 50)

    # Probability is the anchor; raw Score is intentionally weak because the
    # historical relationship was non-monotonic (90-99 underperformed 100).
    base = 0.55 * probability + 0.25 * ai_score + 0.12 * confidence + 0.08 * min(100.0, rr * 25.0)
    adjustment = 0.0
    reasons: List[Dict[str, Any]] = []
    hard_blocks: List[str] = []

    min_liquidity = _env("QUALITY_MIN_QUOTE_VOLUME", 130_000_000)
    if quote_volume and quote_volume < min_liquidity:
        adjustment += _add(reasons, "penalty", "low_liquidity", -9, f"quoteVolume {quote_volume:.0f} < {min_liquidity:.0f}")
        if quote_volume < min_liquidity * 0.65:
            hard_blocks.append("critical_low_liquidity")
    elif quote_volume >= min_liquidity * 2:
        adjustment += _add(reasons, "boost", "high_liquidity", 4, "liquidity comfortably above minimum")

    if setup == "PULLBACK":
        adjustment += _add(reasons, "boost", "pullback", 6, "historically stronger than breakout")
    elif setup == "BREAKOUT":
        adjustment += _add(reasons, "penalty", "breakout", -4, "requires extra confirmation")
        if volume < 60:
            adjustment += _add(reasons, "penalty", "weak_breakout_volume", -7, "breakout with volume factor < 60")

    if capital_flow <= 50:
        adjustment += _add(reasons, "penalty", "weak_capital_flow", -9, "capital flow <= 50")
    elif capital_flow >= 95:
        adjustment += _add(reasons, "boost", "extreme_capital_flow", 7, "capital flow >= 95")
    elif capital_flow >= 70:
        adjustment += _add(reasons, "boost", "strong_capital_flow", 4, "capital flow >= 70")

    if volume >= 65:
        adjustment += _add(reasons, "boost", "strong_volume", 5, "volume factor >= 65")
    elif volume < 50:
        adjustment += _add(reasons, "penalty", "weak_volume", -5, "volume factor < 50")

    if smart_money >= 69:
        adjustment += _add(reasons, "boost", "smart_money", 5, "smart money >= 69")
    elif smart_money <= 45:
        adjustment += _add(reasons, "penalty", "weak_smart_money", -3, "smart money <= 45")

    if alignment >= 90 and trend >= 90:
        adjustment += _add(reasons, "boost", "strong_alignment_trend", 6, "alignment and trend >= 90")
    elif alignment >= 75 and trend >= 80:
        adjustment += _add(reasons, "boost", "aligned_trend", 3, "multi-timeframe alignment")

    if probability >= 72:
        adjustment += _add(reasons, "boost", "high_probability", 6, "probability >= 72")
    elif probability < 60:
        adjustment += _add(reasons, "penalty", "low_probability", -8, "probability < 60")

    if confidence < 36:
        adjustment += _add(reasons, "penalty", "low_confidence", -8, "confidence < 36")
        hard_blocks.append("low_confidence")
    if uncertainty > 64:
        adjustment += _add(reasons, "penalty", "high_uncertainty", -8, "uncertainty > 64")
        hard_blocks.append("high_uncertainty")

    if rr < _env("QUALITY_MIN_RR", 2.3):
        adjustment += _add(reasons, "penalty", "weak_rr", -5, "R/R below quality profile")

    # Empirical LONG-only context. Do not mirror it blindly to SHORT.
    if long_mode:
        if timeframes.get("1d") == "UP":
            adjustment += _add(reasons, "boost", "daily_up", 4, "1D trend agrees with LONG")
        if timeframes.get("5m") == "UP":
            adjustment += _add(reasons, "boost", "entry_tf_up", 3, "5m agrees with LONG")
        elif timeframes.get("5m") in ("DOWN", "RANGE") and setup == "BREAKOUT":
            adjustment += _add(reasons, "penalty", "breakout_entry_tf_conflict", -6, "breakout without 5m UP")
        if timeframes.get("4h") == "DOWN":
            adjustment += _add(reasons, "penalty", "four_hour_conflict", -12, "4H DOWN conflicts with LONG")
            hard_blocks.append("four_hour_conflict")
        if timeframes.get("1h") == "RANGE" and timeframes.get("5m") != "UP":
            adjustment += _add(reasons, "penalty", "range_without_trigger", -5, "1H range without 5m trigger")
        if structure_1h == "SWEEP_HIGH":
            adjustment += _add(reasons, "boost", "structure_1h_sweep_high", 5, "empirically strong in current dataset")
        elif structure_1h == "BOS_UP":
            adjustment += _add(reasons, "boost", "structure_1h_bos_up", 3, "bullish break of structure")
        elif structure_1h == "SWEEP_LOW":
            adjustment += _add(reasons, "penalty", "structure_1h_sweep_low", -4, "weak in current LONG dataset")

    # Strong interactions from the larger dataset.
    if capital_flow >= 62 and alignment >= 75 and volume >= 65:
        adjustment += _add(reasons, "boost", "flow_alignment_volume", 9, "best diversified empirical combination")
    if smart_money >= 60 and setup == "PULLBACK" and volume >= 65:
        adjustment += _add(reasons, "boost", "smart_pullback_volume", 8, "strong interaction")
    if smart_money >= 60 and setup == "PULLBACK" and probability >= 70:
        adjustment += _add(reasons, "boost", "smart_pullback_probability", 7, "strong interaction")

    agreement, chronos_strength = _chronos_agreement(signal)
    if agreement and chronos_strength >= 60:
        adjustment += _add(reasons, "boost", "chronos_agreement", 3, "pretrained forecast agrees")
    elif signal.get("chronos") and not agreement:
        adjustment += _add(reasons, "penalty", "chronos_conflict", -3, "pretrained forecast conflicts")

    max_adjustment = _env("QUALITY_MAX_ADJUSTMENT", 25)
    adjustment = max(-max_adjustment, min(max_adjustment, adjustment))
    quality = max(0.0, min(100.0, base + adjustment))

    min_quality = _env("QUALITY_MIN_SCORE", 72)
    gate_enabled = str(os.getenv("QUALITY_GATE_ENABLED", "true")).lower() in {"1", "true", "yes", "on"}
    passed = quality >= min_quality and not hard_blocks
    decision = "HIGH_QUALITY" if quality >= 80 and not hard_blocks else "TRADE_CANDIDATE" if passed else "NO_TRADE"

    return {
        "qualityScore": round(quality, 1),
        "qualityBase": round(base, 1),
        "qualityAdjustment": round(adjustment, 1),
        "qualityDecision": decision,
        "qualityPassed": bool(passed or not gate_enabled),
        "qualityGateEnabled": gate_enabled,
        "qualityThreshold": min_quality,
        "qualityReasons": sorted(reasons, key=lambda x: abs(x["points"]), reverse=True),
        "qualityHardBlocks": hard_blocks,
        "qualityProfile": "profit-profile-long-v1" if long_mode else "direction-neutral-v1",
    }


def enrich_with_quality(signal: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(signal)
    item.update(evaluate_signal_quality(item))
    return item
