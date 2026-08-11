from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from statistics import mean
from typing import Any


def _f(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def normalize_klines(rows: list[Any]) -> list[dict[str, float]]:
    out = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            out.append({
                "ts": float(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })
        except Exception:
            continue
    return out


def atr(candles: list[dict[str, float]], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))
    sample = trs[-period:]
    return mean(sample) if sample else 0.0


def pivots(candles: list[dict[str, float]], window: int = 2):
    highs, lows = [], []
    n = len(candles)
    for i in range(window, n - window):
        segment = candles[i - window:i + window + 1]
        h = candles[i]["high"]
        l = candles[i]["low"]
        if h >= max(x["high"] for x in segment):
            highs.append((i, h))
        if l <= min(x["low"] for x in segment):
            lows.append((i, l))
    return highs, lows


def _find_recent_up_swing(candles: list[dict[str, float]], lookback: int = 120) -> dict[str, Any] | None:
    if len(candles) < 40:
        return None
    work = candles[-lookback:]
    highs, lows = pivots(work, 2)
    if not highs or not lows:
        return None

    # Prefer the latest meaningful pivot high with a pivot low before it.
    candidate = None
    for hi_idx, hi in reversed(highs):
        prior_lows = [(i, v) for i, v in lows if i < hi_idx - 2]
        if not prior_lows:
            continue
        lo_idx, lo = prior_lows[-1]
        move_pct = (hi / lo - 1.0) * 100 if lo > 0 else 0.0
        if move_pct >= 5.0:
            candidate = (lo_idx, lo, hi_idx, hi, move_pct)
            break
    if candidate is None:
        return None

    lo_idx, lo, hi_idx, hi, move_pct = candidate
    prior_highs = [(i, v) for i, v in highs if i < hi_idx]
    prior_lows = [(i, v) for i, v in lows if i < lo_idx]
    hh = bool(prior_highs and hi > prior_highs[-1][1])
    hl = bool(prior_lows and lo > prior_lows[-1][1])
    sma20 = mean(x["close"] for x in work[-20:])
    close = work[-1]["close"]
    structure_up = hh and hl
    trend_up = structure_up or (close >= sma20 and move_pct >= 8.0)
    return {
        "low_index": lo_idx,
        "low": lo,
        "high_index": hi_idx,
        "high": hi,
        "move_pct": move_pct,
        "higher_high": hh,
        "higher_low": hl,
        "structure_up": structure_up,
        "trend_up": trend_up,
        "sma20": sma20,
        "close": close,
    }


def _support_near_fib(candles: list[dict[str, float]], fib: float, daily_atr: float) -> dict[str, Any]:
    if fib <= 0:
        return {"touches": 0, "low": 0.0, "high": 0.0, "center": 0.0, "tolerance": 0.0}
    tolerance = max(fib * 0.0125, daily_atr * 0.45)
    _, lows = pivots(candles[-120:], 2)
    values = [v for _, v in lows if abs(v - fib) <= tolerance]
    # Closes around the area are useful secondary confirmation, but weaker than pivot lows.
    closes = [x["close"] for x in candles[-90:] if abs(x["close"] - fib) <= tolerance * 0.75]
    all_values = values + closes[-3:]
    if not all_values:
        return {"touches": 0, "low": fib - tolerance, "high": fib + tolerance, "center": fib, "tolerance": tolerance}
    zone_low = max(0.0, min(all_values) - tolerance * 0.25)
    zone_high = max(all_values) + tolerance * 0.25
    return {
        "touches": len(values),
        "close_confirmations": len(closes),
        "low": zone_low,
        "high": zone_high,
        "center": mean(all_values),
        "tolerance": tolerance,
    }


def _h4_trigger(candles: list[dict[str, float]], zone_low: float, zone_high: float) -> dict[str, Any]:
    if len(candles) < 5:
        return {"near": False, "trigger": False, "reason": "Недостаточно H4 данных"}
    cur, prev = candles[-1], candles[-2]
    price = cur["close"]
    zone_center = (zone_low + zone_high) / 2 if zone_high > 0 else price
    distance_pct = abs(price / zone_center - 1.0) * 100 if zone_center > 0 else 999.0
    touched = cur["low"] <= zone_high * 1.003 and cur["high"] >= zone_low * 0.997
    near = touched or distance_pct <= 3.0
    bullish = cur["close"] > cur["open"]
    engulfing = bullish and prev["close"] < prev["open"] and cur["close"] >= prev["open"] and cur["open"] <= prev["close"]
    bos = cur["close"] > prev["high"]
    recent_lows = [x["low"] for x in candles[-5:-1]]
    higher_low = bool(recent_lows and cur["low"] > min(recent_lows))
    trigger = near and bullish and (engulfing or bos or higher_low)
    reasons = []
    if touched: reasons.append("касание зоны")
    if engulfing: reasons.append("bullish engulfing")
    if bos: reasons.append("H4 BOS")
    if higher_low: reasons.append("higher low")
    return {
        "near": near,
        "touched": touched,
        "trigger": trigger,
        "bullish": bullish,
        "engulfing": engulfing,
        "bos": bos,
        "higher_low": higher_low,
        "distance_pct": distance_pct,
        "price": price,
        "reason": ", ".join(reasons) if reasons else "H4 подтверждения пока нет",
    }


def analyze_symbol(symbol: str, quote_volume: float, d1_rows, h4_rows, provider: str | None = None) -> dict[str, Any]:
    d1 = normalize_klines(d1_rows)
    h4 = normalize_klines(h4_rows)
    # Avoid using the currently forming daily candle where possible.
    if len(d1) > 60:
        d1_closed = d1[:-1]
    else:
        d1_closed = d1
    swing = _find_recent_up_swing(d1_closed)
    if not swing:
        return {"symbol": symbol, "status": "NO_SETUP", "reason": "Нет значимого D1 swing", "quote_volume": quote_volume}

    daily_atr = atr(d1_closed, 14)
    fib = swing["low"] + (swing["high"] - swing["low"]) * 0.5
    support = _support_near_fib(d1_closed, fib, daily_atr)
    support_overlap = support["low"] <= fib <= support["high"]
    h4_state = _h4_trigger(h4[:-1] if len(h4) > 30 else h4, support["low"], support["high"])

    entry = (fib + support["center"]) / 2 if support["center"] else fib
    stop = min(fib, support["low"]) - daily_atr * 0.35
    tp = swing["high"] - daily_atr * 0.20
    risk = max(0.0, entry - stop)
    reward = max(0.0, tp - entry)
    rr = reward / risk if risk > 0 else 0.0
    support_ok = support.get("touches", 0) >= 2 or (support.get("touches", 0) >= 1 and support.get("close_confirmations", 0) >= 2)
    valid_d1 = bool(swing["trend_up"] and support_overlap and support_ok and tp > entry > stop)

    score = 0.0
    score += 25 if swing["trend_up"] else 0
    score += 10 if swing["structure_up"] else 5
    score += min(20, support.get("touches", 0) * 7)
    score += 10 if support_overlap else 0
    score += 10 if h4_state.get("near") else max(0, 10 - h4_state.get("distance_pct", 99) * 2)
    score += 15 if h4_state.get("trigger") else 0
    score += 10 if rr >= 2.0 else max(0, rr * 5)
    score = round(min(100.0, score), 1)

    if not valid_d1:
        status = "NO_SETUP"
        if not swing["trend_up"]:
            reason = "D1 тренд не восходящий"
        elif not support_ok:
            reason = "Слабая поддержка около Fib 0.5"
        else:
            reason = "Нет confluence Fib/support"
    elif h4_state.get("trigger") and rr >= 2.0:
        status = "READY"
        reason = "D1 trend + Fib 0.5 + support + H4 trigger"
    elif h4_state.get("near"):
        status = "WATCH"
        reason = "Цена у зоны, ждём H4 подтверждение"
    else:
        status = "WAITING"
        reason = "D1 сетап есть, цена ещё не в зоне входа"

    fp_raw = f"fib05|{symbol}|{round(swing['low'],8)}|{round(swing['high'],8)}|{round(entry,8)}"
    fingerprint = hashlib.sha256(fp_raw.encode()).hexdigest()
    return {
        "strategy": "fib_05_pullback",
        "fingerprint": fingerprint,
        "symbol": symbol,
        "status": status,
        "reason": reason,
        "quote_volume": float(quote_volume or 0),
        "provider": provider,
        "trend": "UP" if swing["trend_up"] else "NOT_UP",
        "structure_up": swing["structure_up"],
        "d1_low": swing["low"],
        "d1_high": swing["high"],
        "d1_move_pct": swing["move_pct"],
        "fib_05": fib,
        "support_low": support["low"],
        "support_high": support["high"],
        "support_touches": support.get("touches", 0),
        "entry_price": entry,
        "entry_zone_low": support["low"],
        "entry_zone_high": support["high"],
        "stop_price": stop,
        "tp_price": tp,
        "rr": round(rr, 2),
        "h4_near": bool(h4_state.get("near")),
        "h4_trigger": bool(h4_state.get("trigger")),
        "h4_reason": h4_state.get("reason"),
        "market_price": h4_state.get("price") or (h4[-1]["close"] if h4 else 0),
        "distance_to_zone_pct": round(float(h4_state.get("distance_pct") or 0), 2),
        "score": score,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
