from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any

from strategies.fib_pullback import analyze_symbol as analyze_fib_symbol, atr, normalize_klines, pivots
from order_block_analyzer import analyze_order_blocks
from fvg_analyzer import analyze_fvg


def _f(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    period = max(1, int(period))
    alpha = 2.0 / (period + 1.0)
    value = float(values[0])
    for x in values[1:]:
        value = alpha * float(x) + (1.0 - alpha) * value
    return value


def _sma(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    sample = values[-period:]
    return mean(sample) if sample else 0.0


def _rsi_series(candles: list[dict[str, float]], period: int = 14) -> list[float | None]:
    closes = [x["close"] for x in candles]
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    rs = avg_gain / avg_loss if avg_loss > 0 else math.inf
    out[period] = 100.0 - 100.0 / (1.0 + rs) if math.isfinite(rs) else 100.0
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain, loss = max(0.0, diff), max(0.0, -diff)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else math.inf
        out[i] = 100.0 - 100.0 / (1.0 + rs) if math.isfinite(rs) else 100.0
    return out


def _trend(d1: list[dict[str, float]]) -> dict[str, Any]:
    closes = [x["close"] for x in d1]
    if len(closes) < 60:
        return {"direction": "RANGE", "ema50": _ema(closes, 50), "ema200": _ema(closes, min(200, len(closes))), "strength": 0.0}
    e50 = _ema(closes[-220:], 50)
    e200 = _ema(closes[-240:], 200 if len(closes) >= 200 else max(80, len(closes)))
    price = closes[-1]
    spread = abs(e50 / e200 - 1.0) * 100 if e200 else 0.0
    if price > e50 > e200:
        direction = "UP"
    elif price < e50 < e200:
        direction = "DOWN"
    else:
        direction = "RANGE"
    return {"direction": direction, "ema50": e50, "ema200": e200, "strength": spread, "price": price}


def _fp(strategy: str, symbol: str, direction: str, *parts: float) -> str:
    raw = "|".join([strategy, symbol, direction] + [str(round(_f(x), 8)) for x in parts])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _base(strategy: str, symbol: str, direction: str, status: str, reason: str, quote_volume: float, provider: str | None,
          entry: float, stop: float, tp: float, score: float, market: float, entry_mode: str = "LIMIT", **extra) -> dict[str, Any]:
    risk = abs(entry - stop)
    reward = abs(tp - entry)
    rr = reward / risk if risk > 0 else 0.0
    return {
        "strategy": strategy,
        "fingerprint": _fp(strategy, symbol, direction, entry, stop, tp),
        "symbol": symbol,
        "direction": direction,
        "status": status,
        "reason": reason,
        "quote_volume": float(quote_volume or 0),
        "provider": provider,
        "entry_mode": entry_mode,
        "entry_price": float(entry or 0),
        "entry_zone_low": float(extra.pop("entry_zone_low", entry) or entry or 0),
        "entry_zone_high": float(extra.pop("entry_zone_high", entry) or entry or 0),
        "stop_price": float(stop or 0),
        "tp_price": float(tp or 0),
        "rr": round(rr, 2),
        "score": round(max(0.0, min(100.0, score)), 1),
        "market_price": float(market or 0),
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def _closed(rows, min_len=30):
    data = normalize_klines(rows)
    if len(data) > min_len:
        return data[:-1]
    return data


def _recent_pivot_level(candles: list[dict[str, float]], side: str, lookback=60) -> tuple[int, float] | None:
    work = candles[-lookback:]
    highs, lows = pivots(work, 2)
    values = highs if side == "high" else lows
    return values[-1] if values else None


def _confirmation(candles: list[dict[str, float]], direction: str) -> dict[str, bool]:
    if len(candles) < 3:
        return {"bull": False, "bear": False, "bos": False, "engulf": False, "reject": False}
    cur, prev = candles[-1], candles[-2]
    bull = cur["close"] > cur["open"]
    bear = cur["close"] < cur["open"]
    if direction == "LONG":
        engulf = bull and prev["close"] < prev["open"] and cur["close"] >= prev["open"] and cur["open"] <= prev["close"]
        bos = cur["close"] > max(x["high"] for x in candles[-4:-1])
        body = abs(cur["close"] - cur["open"])
        lower_wick = min(cur["open"], cur["close"]) - cur["low"]
        reject = bull and lower_wick > body * 0.8
    else:
        engulf = bear and prev["close"] > prev["open"] and cur["close"] <= prev["open"] and cur["open"] >= prev["close"]
        bos = cur["close"] < min(x["low"] for x in candles[-4:-1])
        body = abs(cur["close"] - cur["open"])
        upper_wick = cur["high"] - max(cur["open"], cur["close"])
        reject = bear and upper_wick > body * 0.8
    return {"bull": bull, "bear": bear, "engulf": engulf, "bos": bos, "reject": reject}



def _legacy_candles(candles: list[dict[str, float]]) -> list[dict[str, float]]:
    """Adapt normalized Strategy Lab candles to legacy OB/FVG analyzers."""
    return [
        {
            "open_time": x.get("ts"),
            "open": x["open"], "high": x["high"], "low": x["low"],
            "close": x["close"], "volume": x.get("volume", 0.0),
        }
        for x in candles
    ]


def _zone_for_direction(report: dict[str, Any], direction: str, kind: str) -> dict[str, Any] | None:
    if not isinstance(report, dict) or not report.get("available"):
        return None
    if kind == "ob":
        return report.get("nearestBullish") if direction == "LONG" else report.get("nearestBearish")
    return report.get("nearestBullish") if direction == "LONG" else report.get("nearestBearish")


def _zone_near_market(zone: dict[str, Any] | None, max_distance_pct: float = 1.5) -> bool:
    if not zone:
        return False
    try:
        return bool(zone.get("insideNow")) or float(zone.get("distancePercent") or 999) <= max_distance_pct
    except Exception:
        return False


def analyze_ma_ribbon_cross(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    """Bullish MA-ribbon strategy built around 8/13/21/55.

    A slow 55-period average crossing *above* faster 8/13/21 averages is normally
    bearish, not bullish. For BUY we therefore use the economically consistent
    interpretation: the fast ribbon (EMA8 + SMA13 + SMA21) transitions from below
    SMA55 to above it, then waits for price confirmation. The literal 55-over-fast
    event is still detected and exposed in the payload for research/debugging.
    """
    d1 = _closed(d1_rows, 100)
    h4 = _closed(h4_rows, 100)
    if len(d1) < 80 or len(h4) < 70:
        return {"strategy": "ma_ribbon_cross", "symbol": symbol, "status": "NO_SETUP", "reason": "Недостаточно D1/H4 данных"}

    closes = [x["close"] for x in h4]
    volumes = [x.get("volume", 0.0) for x in h4]

    def ema_at(end: int, period: int) -> float:
        sample = closes[:end]
        return _ema(sample, period)

    def sma_at(end: int, period: int) -> float:
        sample = closes[:end]
        return _sma(sample, period)

    # Build the last several closed-bar states so a cross is not lost simply
    # because the newest bar is already the confirmation bar.
    states = []
    start = max(56, len(h4) - 7)
    for end in range(start, len(h4) + 1):
        e8 = ema_at(end, 8)
        m13 = sma_at(end, 13)
        m21 = sma_at(end, 21)
        m55 = sma_at(end, 55)
        states.append({"end": end, "ema8": e8, "ma13": m13, "ma21": m21, "ma55": m55, "close": closes[end - 1]})
    if len(states) < 4:
        return {"strategy": "ma_ribbon_cross", "symbol": symbol, "status": "NO_SETUP", "reason": "Недостаточно MA истории"}

    current = states[-1]
    previous = states[-2]

    def fast_above(st):
        return st["ema8"] > st["ma55"] and st["ma13"] > st["ma55"] and st["ma21"] > st["ma55"]

    def fast_below(st):
        return st["ema8"] < st["ma55"] and st["ma13"] < st["ma55"] and st["ma21"] < st["ma55"]

    # Detect a coordinated transition during the last three closed H4 bars.
    bullish_cross = False
    cross_bars_ago = None
    for i in range(max(1, len(states) - 3), len(states)):
        if fast_above(states[i]) and not fast_above(states[i - 1]):
            # At least two fast components must have moved from/below MA55 to above.
            before = states[i - 1]
            after = states[i]
            crossed = sum((
                before["ema8"] <= before["ma55"] and after["ema8"] > after["ma55"],
                before["ma13"] <= before["ma55"] and after["ma13"] > after["ma55"],
                before["ma21"] <= before["ma55"] and after["ma21"] > after["ma55"],
            ))
            if crossed >= 2:
                bullish_cross = True
                cross_bars_ago = len(states) - 1 - i

    # Literal interpretation requested by the user: SMA55 crosses above all
    # faster lines. Track it, but do not emit BUY because it usually means the
    # fast ribbon has weakened below the slow trend average.
    literal_55_cross_up = (
        previous["ma55"] <= previous["ema8"]
        and previous["ma55"] <= previous["ma13"]
        and previous["ma55"] <= previous["ma21"]
        and current["ma55"] > current["ema8"]
        and current["ma55"] > current["ma13"]
        and current["ma55"] > current["ma21"]
    )

    tr = _trend(d1)
    cur = h4[-1]
    a = max(atr(h4, 14), cur["close"] * 0.002)
    clean_stack = cur["close"] > current["ema8"] > current["ma13"] > current["ma21"] > current["ma55"]
    ma55_prev3 = states[-4]["ma55"]
    ma55_slope_pct = (current["ma55"] / ma55_prev3 - 1.0) * 100 if ma55_prev3 else 0.0
    slope_ok = ma55_slope_pct > 0.0

    avg_vol20 = mean(volumes[-21:-1]) if len(volumes) >= 21 else mean(volumes[:-1]) if len(volumes) > 1 else 0.0
    volume_ratio = cur.get("volume", 0.0) / avg_vol20 if avg_vol20 > 0 else 1.0
    volume_ok = volume_ratio >= 1.10

    rsi_series = _rsi_series(h4, 14)
    rsi = rsi_series[-1] if rsi_series and rsi_series[-1] is not None else 50.0
    rsi_ok = 52.0 <= float(rsi) <= 72.0

    conf = _confirmation(h4, "LONG")
    structure_ok = bool(conf.get("bos") or conf.get("engulf") or conf.get("reject") or cur["close"] > max(x["high"] for x in h4[-5:-1]))
    d1_ok = tr["direction"] == "UP"

    # Avoid buying an already stretched candle after a delayed moving-average cross.
    extension_atr = (cur["close"] - current["ema8"]) / a if a > 0 else 0.0
    not_overextended = extension_atr <= 1.5

    recent_cross_or_alignment = bullish_cross or fast_above(current)
    quality_votes = sum((d1_ok, clean_stack, slope_ok, volume_ok, rsi_ok, structure_ok, not_overextended))

    # READY deliberately requires the actual recent cross plus the strongest
    # higher-timeframe/stack filters. Volume/RSI/structure act as additional
    # quality votes rather than three hard filters, which keeps signal count usable.
    ready = bullish_cross and d1_ok and clean_stack and slope_ok and not_overextended and quality_votes >= 6
    watch = recent_cross_or_alignment and clean_stack and slope_ok and quality_votes >= 4

    if literal_55_cross_up and not fast_above(current):
        status = "NO_SETUP"
        reason = "SMA55 пересекла EMA8/SMA13/SMA21 снизу вверх — это медвежье, а не BUY-событие"
    elif ready:
        status = "READY"
        reason = "Fast ribbon пересекла SMA55 вверх + D1 UP + bullish H4 confirmation"
    elif watch:
        status = "WATCH"
        reason = "Bullish MA stack сформирован; не хватает части quality-confirmations или свежего cross"
    elif tr["direction"] == "UP" and not fast_above(current):
        status = "WAITING"
        reason = "D1 тренд вверх; ждём переход EMA8/SMA13/SMA21 выше SMA55"
    else:
        status = "NO_SETUP"
        reason = "Нет подтверждённого bullish MA-ribbon setup"

    # Entry is a stop-confirmation above the closed H4 signal candle. This avoids
    # entering merely because averages crossed while price immediately rolls over.
    entry = max(cur["high"], cur["close"]) + a * 0.05
    recent_low = min(x["low"] for x in h4[-10:])
    stop = min(recent_low, current["ma55"] - a * 0.25)
    if stop >= entry:
        stop = entry - a * 1.5
    risk = max(entry - stop, a * 0.75)
    d1_high = max(x["high"] for x in d1[-60:])
    tp_rr = entry + 2.5 * risk
    # Use D1 high only if it offers at least 2R; otherwise keep 2.5R target.
    tp = d1_high if d1_high >= entry + 2.0 * risk else tp_rr

    score = 25.0
    score += 18 if bullish_cross else (8 if fast_above(current) else 0)
    score += 15 if d1_ok else 0
    score += 12 if clean_stack else 0
    score += 10 if slope_ok else 0
    score += 8 if volume_ok else 0
    score += 7 if rsi_ok else 0
    score += 8 if structure_ok else 0
    score += 5 if not_overextended else -8

    return _base(
        "ma_ribbon_cross", symbol, "LONG", status, reason, quote_volume, provider,
        entry, stop, tp, score, cur["close"], "STOP",
        ema8=current["ema8"], ma13=current["ma13"], ma21=current["ma21"], ma55=current["ma55"],
        bullish_cross=bullish_cross, cross_bars_ago=cross_bars_ago,
        literal_55_cross_up=literal_55_cross_up,
        clean_stack=clean_stack, ma55_slope_pct=round(ma55_slope_pct, 4),
        d1_trend=tr["direction"], volume_ratio=round(volume_ratio, 3), rsi=round(float(rsi), 2),
        structure_confirmation=structure_ok, confirmation=conf,
        extension_atr=round(extension_atr, 3), quality_votes=quality_votes,
        d1_high=d1_high,
    )



def _ma55_cycle_states(h4: list[dict[str, float]], tail: int = 12) -> list[dict[str, Any]]:
    """Return closed-bar MA states used by MA55 cycle entry/exit logic."""
    closes = [x["close"] for x in h4]
    states: list[dict[str, Any]] = []
    if len(closes) < 56:
        return states
    start = max(56, len(h4) - max(4, tail) + 1)
    for end in range(start, len(h4) + 1):
        sample = closes[:end]
        st = {
            "end": end,
            "ts": h4[end - 1].get("ts"),
            "close": closes[end - 1],
            "ema8": _ema(sample, 8),
            "ma13": _sma(sample, 13),
            "ma21": _sma(sample, 21),
            "ma55": _sma(sample, 55),
        }
        st["slow_above_all"] = st["ma55"] >= st["ema8"] and st["ma55"] >= st["ma13"] and st["ma55"] >= st["ma21"]
        st["slow_below_all"] = st["ma55"] <= st["ema8"] and st["ma55"] <= st["ma13"] and st["ma55"] <= st["ma21"]
        states.append(st)
    return states


def ma55_cycle_event(h4_rows, direction: str, lookback_bars: int = 3) -> dict[str, Any] | None:
    """Detect completed transition of SMA55 across all three fast averages.

    direction='BUY': 55 transitions from above-all to below-all.
    direction='EXIT': 55 transitions from below-all to above-all.
    Crosses may complete over up to lookback_bars closed H4 candles, which is
    more robust than requiring all three mathematical intersections in one bar.
    """
    h4 = normalize_klines(h4_rows)
    states = _ma55_cycle_states(h4, tail=max(8, lookback_bars + 4))
    if len(states) < 2:
        return None
    current = states[-1]
    direction = str(direction or "BUY").upper()
    target = "slow_below_all" if direction == "BUY" else "slow_above_all"
    origin = "slow_above_all" if direction == "BUY" else "slow_below_all"
    if not current[target]:
        return None
    # Only emit on the first fully-crossed state, not every later scan.
    if states[-2][target]:
        return None
    window = states[max(0, len(states) - 1 - lookback_bars):-1]
    origin_state = next((x for x in reversed(window) if x[origin]), None)
    if not origin_state:
        return None
    return {
        "type": direction,
        "ts": current.get("ts"),
        "price": current.get("close"),
        "bars": max(1, current["end"] - origin_state["end"]),
        "state": current,
        "origin": origin_state,
    }


def analyze_ma55_cycle(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    d1 = _closed(d1_rows, 100)
    h4 = _closed(h4_rows, 100)
    if len(d1) < 80 or len(h4) < 70:
        return {"strategy": "ma55_cycle", "symbol": symbol, "status": "NO_SETUP", "reason": "Недостаточно D1/H4 данных"}

    buy_event = ma55_cycle_event(h4, "BUY", 12)
    states = _ma55_cycle_states(h4, tail=10)
    current = states[-1] if states else None
    if not current:
        return {"strategy": "ma55_cycle", "symbol": symbol, "status": "NO_SETUP", "reason": "Недостаточно MA истории"}

    tr = _trend(d1)
    cur = h4[-1]
    a = max(atr(h4, 14), cur["close"] * 0.002)
    volumes = [x.get("volume", 0.0) for x in h4]
    avg_vol20 = mean(volumes[-21:-1]) if len(volumes) >= 21 else mean(volumes[:-1]) if len(volumes) > 1 else 0.0
    volume_ratio = cur.get("volume", 0.0) / avg_vol20 if avg_vol20 > 0 else 1.0
    rsi_series = _rsi_series(h4, 14)
    rsi = float(rsi_series[-1]) if rsi_series and rsi_series[-1] is not None else 50.0
    conf = _confirmation(h4, "LONG")

    bullish_stack = cur["close"] > current["ema8"] > current["ma13"] > current["ma21"] > current["ma55"]
    ma55_old = states[-4]["ma55"] if len(states) >= 4 else states[0]["ma55"]
    ma55_slope_pct = (current["ma55"] / ma55_old - 1.0) * 100 if ma55_old else 0.0
    slope_ok = ma55_slope_pct > 0
    d1_ok = tr["direction"] == "UP"
    rsi_ok = 50.0 <= rsi <= 72.0
    volume_ok = volume_ratio >= 1.05
    structure_ok = bool(conf.get("bos") or conf.get("engulf") or conf.get("reject"))
    extension_atr = (cur["close"] - current["ema8"]) / a if a > 0 else 0.0
    not_overextended = extension_atr <= 1.5

    votes = sum((d1_ok, bullish_stack, slope_ok, rsi_ok, volume_ok, structure_ok, not_overextended))
    ready = bool(buy_event) and d1_ok and bullish_stack and slope_ok and not_overextended and votes >= 5
    watch = current["slow_below_all"] and bullish_stack and slope_ok and votes >= 4

    if ready:
        status = "READY"
        reason = "SMA55 прошла EMA8/SMA13/SMA21 сверху вниз + подтверждён bullish regime"
    elif watch:
        status = "WATCH"
        reason = "SMA55 уже ниже fast ribbon; ждём новый подтверждённый цикл/quality confirmation"
    elif tr["direction"] == "UP":
        status = "WAITING"
        reason = "D1 UP; ждём, когда SMA55 завершит переход сверху вниз через EMA8/SMA13/SMA21"
    else:
        status = "NO_SETUP"
        reason = "Нет D1 bullish regime или подтверждённого MA55-cycle"

    # Signal reference price is the closed H4 price. Forward tracker records the
    # actual entry on the first future 1H bar after discovery (NEXT_BAR_MARKET).
    entry = cur["close"]
    recent_low = min(x["low"] for x in h4[-12:])
    stop = min(recent_low, current["ma55"] - a * 0.50)
    if stop >= entry:
        stop = entry - 2.0 * a
    risk = max(entry - stop, a)
    # No normal take-profit: reverse MA55 cross is the intended exit. tp_price is
    # only a schema-compatible reference and is explicitly ignored by tracker.
    tp = entry + 6.0 * risk

    score = 30.0 + (22 if buy_event else 0) + (14 if d1_ok else 0) + (12 if bullish_stack else 0)
    score += 8 if slope_ok else 0
    score += 6 if rsi_ok else 0
    score += 5 if volume_ok else 0
    score += 6 if structure_ok else 0
    score += 5 if not_overextended else -10

    out = _base(
        "ma55_cycle", symbol, "LONG", status, reason, quote_volume, provider,
        entry, stop, tp, score, cur["close"], "NEXT_BAR_MARKET",
        ema8=current["ema8"], ma13=current["ma13"], ma21=current["ma21"], ma55=current["ma55"],
        ma55_slope_pct=round(ma55_slope_pct, 4), d1_trend=tr["direction"], rsi=round(rsi, 2),
        volume_ratio=round(volume_ratio, 3), structure_confirmation=structure_ok,
        extension_atr=round(extension_atr, 3), quality_votes=votes,
        buy_cross=bool(buy_event), cross_bars=(buy_event or {}).get("bars"),
        exit_mode="MA55_CROSS_UP_ALL", ignore_tp=True, reference_tp=tp,
        protective_stop=True,
    )
    if buy_event and buy_event.get("ts") is not None:
        out["fingerprint"] = _fp("ma55_cycle", symbol, "LONG", float(buy_event["ts"]))
        out["cross_ts"] = buy_event["ts"]
    return out

def analyze_smart_money_confluence(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    """Rule-based SMC strategy.

    SMC labels are treated as testable price-action features, not proof of
    institutional intent. READY requires confluence instead of a single FVG/OB.
    """
    d1 = _closed(d1_rows, 90)
    h4 = _closed(h4_rows, 90)
    h1 = _closed((derivatives or {}).get("h1_rows") or [], 100)
    if len(d1) < 80 or len(h4) < 70 or len(h1) < 80:
        return {"strategy":"smart_money_confluence","symbol":symbol,"status":"NO_SETUP","reason":"Недостаточно D1/H4/H1 данных"}

    tr = _trend(d1)
    a4 = atr(h4, 14)
    a1 = atr(h1, 14)
    cur4 = h4[-1]
    cur1 = h1[-1]
    prior4 = h4[-60:-4]
    external_low = min(x["low"] for x in prior4)
    external_high = max(x["high"] for x in prior4)
    range_mid = (external_low + external_high) / 2.0

    # Detect sweep/reclaim over the last three closed H4 bars so a valid event
    # is not lost just because the newest bar is the confirmation bar.
    sweep_long = None
    sweep_short = None
    for x in h4[-3:]:
        if x["low"] < external_low - a4 * 0.08 and x["close"] > external_low:
            sweep_long = x
        if x["high"] > external_high + a4 * 0.08 and x["close"] < external_high:
            sweep_short = x

    # Direction preference: actual sweep wins; otherwise HTF bias + proximity.
    if sweep_long and not sweep_short:
        direction = "LONG"
    elif sweep_short and not sweep_long:
        direction = "SHORT"
    elif tr["direction"] == "UP":
        direction = "LONG"
    elif tr["direction"] == "DOWN":
        direction = "SHORT"
    else:
        direction = "LONG" if cur4["close"] <= range_mid else "SHORT"

    ob = analyze_order_blocks(_legacy_candles(h1))
    fvg = analyze_fvg(_legacy_candles(h1))
    ob_zone = _zone_for_direction(ob, direction, "ob")
    fvg_zone = _zone_for_direction(fvg, direction, "fvg")
    ob_near = _zone_near_market(ob_zone, 1.8)
    fvg_near = _zone_near_market(fvg_zone, 1.8)
    conf = _confirmation(h1, direction)
    structure_shift = bool(conf.get("bos") or conf.get("engulf") or conf.get("reject"))
    sweep = bool(sweep_long if direction == "LONG" else sweep_short)
    premium_discount = cur4["close"] <= range_mid if direction == "LONG" else cur4["close"] >= range_mid

    # Optional derivatives confirmation; never fabricate zero when unavailable.
    funding = _extract_funding((derivatives or {}).get("premium"))
    oi_change = _oi_change_pct(derivatives or {})
    derivatives_support = False
    if funding is not None:
        derivatives_support |= (direction == "LONG" and funding <= 0) or (direction == "SHORT" and funding >= 0)
    if oi_change is not None:
        derivatives_support |= oi_change > 0

    # Prefer the overlap/nearest institutional zone as context, but trigger entry
    # only after H1 structure confirmation. That avoids a historical limit fill.
    zones = [z for z in (ob_zone, fvg_zone) if z]
    if zones:
        zone_low = max(0.0, min(float(z.get("low") or cur1["close"]) for z in zones))
        zone_high = max(float(z.get("high") or cur1["close"]) for z in zones)
    else:
        zone_low = cur1["close"] - a1 * 0.35
        zone_high = cur1["close"] + a1 * 0.35

    if direction == "LONG":
        sweep_extreme = min((sweep_long or cur4)["low"], external_low)
        entry = max(cur1["high"], cur1["close"]) + a1 * 0.03
        stop = sweep_extreme - a1 * 0.25
        target_liquidity = external_high - a4 * 0.10
        tp = max(target_liquidity, entry + 2.2 * max(entry - stop, a1 * 0.5))
    else:
        sweep_extreme = max((sweep_short or cur4)["high"], external_high)
        entry = min(cur1["low"], cur1["close"]) - a1 * 0.03
        stop = sweep_extreme + a1 * 0.25
        target_liquidity = external_low + a4 * 0.10
        tp = min(target_liquidity, entry - 2.2 * max(stop - entry, a1 * 0.5))

    risk = abs(entry - stop)
    reward = abs(tp - entry)
    rr = reward / risk if risk > 0 else 0.0
    confluence_count = sum((sweep, structure_shift, ob_near, fvg_near, premium_discount))
    zone_confluence = ob_near or fvg_near
    ready = sweep and structure_shift and zone_confluence and rr >= 2.0
    near = confluence_count >= 3 or (zone_confluence and premium_discount)
    status = "READY" if ready else ("WATCH" if near else "WAITING")
    if not sweep and not zone_confluence and confluence_count < 2:
        status = "NO_SETUP"

    score = 20.0
    score += 22 if sweep else 0
    score += 22 if structure_shift else 0
    score += 12 if ob_near else 0
    score += 12 if fvg_near else 0
    score += 7 if premium_discount else 0
    score += 5 if derivatives_support else 0
    score += min(8, max(0, rr - 1.5) * 4)

    if status == "READY":
        reason = "Liquidity sweep + H1 structure shift + OB/FVG confluence"
    elif status == "WATCH":
        reason = "SMC confluence формируется; ждём полный sweep/structure trigger"
    elif status == "WAITING":
        reason = "HTF context есть, цена/структура ещё не дали SMC trigger"
    else:
        reason = "Недостаточно независимых SMC подтверждений"

    return _base(
        "smart_money_confluence", symbol, direction, status, reason, quote_volume, provider,
        entry, stop, tp, score, cur1["close"], "STOP",
        entry_zone_low=zone_low, entry_zone_high=zone_high,
        external_liquidity_low=external_low, external_liquidity_high=external_high,
        premium_discount="discount" if cur4["close"] <= range_mid else "premium",
        liquidity_sweep=sweep, structure_shift=structure_shift,
        order_block_near=ob_near, fvg_near=fvg_near,
        order_block=ob_zone, fvg=fvg_zone,
        funding_rate=funding, oi_change_pct=oi_change,
        derivatives_support=derivatives_support,
        confluence_count=confluence_count,
        confirmation=conf,
    )

def analyze_liquidity_sweep(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    d1, h4 = _closed(d1_rows, 60), _closed(h4_rows, 40)
    if len(h4) < 35:
        return {"strategy": "liquidity_sweep_reclaim", "symbol": symbol, "status": "NO_SETUP", "reason": "Недостаточно H4 данных"}
    a = atr(h4, 14)
    cur = h4[-1]
    prior = h4[-35:-3]
    prior_low = min(x["low"] for x in prior)
    prior_high = max(x["high"] for x in prior)
    long_sweep = cur["low"] < prior_low - a * 0.10 and cur["close"] > prior_low
    short_sweep = cur["high"] > prior_high + a * 0.10 and cur["close"] < prior_high
    dist_low = abs(cur["close"] / prior_low - 1) * 100 if prior_low else 999
    dist_high = abs(cur["close"] / prior_high - 1) * 100 if prior_high else 999
    near_long, near_short = dist_low <= 2.0, dist_high <= 2.0
    if long_sweep or (near_long and not short_sweep):
        direction = "LONG"; level = prior_low; extreme = cur["low"]
    elif short_sweep or near_short:
        direction = "SHORT"; level = prior_high; extreme = cur["high"]
    else:
        return {"strategy": "liquidity_sweep_reclaim", "symbol": symbol, "status": "NO_SETUP", "reason": "Нет sweep/reclaim около H4 ликвидности"}
    conf = _confirmation(h4, direction)
    swept = long_sweep if direction == "LONG" else short_sweep
    confirmed = swept and (conf["engulf"] or conf["bos"] or conf["reject"])
    if direction == "LONG":
        entry = max(cur["high"], cur["close"]) + a * 0.03
        stop = min(extreme, level) - a * 0.20
        opposite = prior_high
        tp = max(entry + 2.2 * (entry - stop), opposite - a * 0.10)
    else:
        entry = min(cur["low"], cur["close"]) - a * 0.03
        stop = max(extreme, level) + a * 0.20
        opposite = prior_low
        tp = min(entry - 2.2 * (stop - entry), opposite + a * 0.10)
    status = "READY" if confirmed else ("WATCH" if swept else "WAITING")
    reason = "Sweep + reclaim + H4 confirmation" if confirmed else ("Sweep/reclaim есть, ждём structure confirmation" if swept else "Цена рядом с liquidity level")
    score = 45 + (20 if swept else 0) + (20 if confirmed else 0) + min(15, float(quote_volume or 0) / 100_000_000 * 3)
    return _base("liquidity_sweep_reclaim", symbol, direction, status, reason, quote_volume, provider, entry, stop, tp, score, cur["close"], "STOP",
                 liquidity_level=level, sweep_extreme=extreme, confirmation=conf)


def analyze_ema_pullback(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    d1, h4 = _closed(d1_rows, 80), _closed(h4_rows, 50)
    if len(d1) < 80 or len(h4) < 50:
        return {"strategy": "ema_trend_pullback", "symbol": symbol, "status": "NO_SETUP", "reason": "Недостаточно данных"}
    tr = _trend(d1)
    if tr["direction"] == "RANGE":
        return {"strategy": "ema_trend_pullback", "symbol": symbol, "status": "NO_SETUP", "reason": "Нет D1 EMA тренда"}
    direction = "LONG" if tr["direction"] == "UP" else "SHORT"
    closes = [x["close"] for x in h4]
    ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
    zone_low, zone_high = sorted((ema20, ema50))
    cur = h4[-1]; a = atr(h4, 14)
    distance = 0.0 if zone_low <= cur["close"] <= zone_high else min(abs(cur["close"] / zone_low - 1), abs(cur["close"] / zone_high - 1)) * 100
    touched = cur["low"] <= zone_high * 1.002 and cur["high"] >= zone_low * 0.998
    conf = _confirmation(h4, direction)
    aligned = (direction == "LONG" and ema20 > ema50) or (direction == "SHORT" and ema20 < ema50)
    confirmed = touched and aligned and (conf["engulf"] or conf["bos"] or conf["reject"])
    entry = (zone_low + zone_high) / 2
    if direction == "LONG":
        stop = min(x["low"] for x in h4[-10:]) - a * 0.25
        high = max(x["high"] for x in d1[-40:])
        tp = max(high - a * 0.1, entry + 2.2 * max(entry - stop, a * 0.5))
    else:
        stop = max(x["high"] for x in h4[-10:]) + a * 0.25
        low = min(x["low"] for x in d1[-40:])
        tp = min(low + a * 0.1, entry - 2.2 * max(stop - entry, a * 0.5))
    status = "READY" if confirmed else ("WATCH" if touched or distance <= 2.0 else "WAITING")
    reason = "D1 trend + EMA pullback + H4 confirmation" if confirmed else ("Цена у EMA-zone, ждём подтверждение" if status == "WATCH" else "Тренд есть, ждём откат к EMA-zone")
    score = 40 + min(15, tr["strength"] * 5) + (15 if aligned else 0) + (15 if touched else 0) + (15 if confirmed else 0)
    return _base("ema_trend_pullback", symbol, direction, status, reason, quote_volume, provider, entry, stop, tp, score, cur["close"], "LIMIT",
                 entry_zone_low=zone_low, entry_zone_high=zone_high, ema20=ema20, ema50=ema50, d1_trend=tr["direction"], distance_to_zone_pct=distance)


def analyze_breakout_retest(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    h4 = _closed(h4_rows, 45)
    if len(h4) < 45:
        return {"strategy": "breakout_retest", "symbol": symbol, "status": "NO_SETUP", "reason": "Недостаточно H4 данных"}
    a = atr(h4, 14); cur = h4[-1]
    prior = h4[-45:-8]
    resistance = max(x["high"] for x in prior)
    support = min(x["low"] for x in prior)
    recent = h4[-8:]
    long_break_idx = next((i for i, x in enumerate(recent[:-1]) if x["close"] > resistance + a * 0.08), None)
    short_break_idx = next((i for i, x in enumerate(recent[:-1]) if x["close"] < support - a * 0.08), None)
    long_retest = long_break_idx is not None and cur["low"] <= resistance + a * 0.20 and cur["close"] > resistance
    short_retest = short_break_idx is not None and cur["high"] >= support - a * 0.20 and cur["close"] < support
    if long_retest:
        direction="LONG"; level=resistance
    elif short_retest:
        direction="SHORT"; level=support
    else:
        dlong = abs(cur["close"] / resistance - 1)*100 if resistance else 999
        dshort = abs(cur["close"] / support - 1)*100 if support else 999
        if long_break_idx is not None:
            direction="LONG"; level=resistance
        elif short_break_idx is not None:
            direction="SHORT"; level=support
        elif min(dlong, dshort) <= 1.2:
            direction="LONG" if dlong <= dshort else "SHORT"; level=resistance if direction=="LONG" else support
        else:
            return {"strategy": "breakout_retest", "symbol": symbol, "status": "NO_SETUP", "reason": "Нет breakout/retest структуры"}
    conf = _confirmation(h4, direction)
    retest = long_retest if direction=="LONG" else short_retest
    confirmed = retest and (conf["engulf"] or conf["bos"] or conf["reject"])
    if direction=="LONG":
        entry=max(cur["high"],cur["close"])+a*0.03; stop=min(cur["low"],level-a*0.15)-a*0.1; tp=entry+2.5*(entry-stop)
    else:
        entry=min(cur["low"],cur["close"])-a*0.03; stop=max(cur["high"],level+a*0.15)+a*0.1; tp=entry-2.5*(stop-entry)
    status="READY" if confirmed else ("WATCH" if retest else "WAITING")
    reason="Breakout + retest + confirmation" if confirmed else ("Retest есть, ждём confirmation" if retest else "Breakout/уровень есть, ждём retest")
    score=40+(20 if (long_break_idx is not None or short_break_idx is not None) else 0)+(20 if retest else 0)+(20 if confirmed else 0)
    return _base("breakout_retest",symbol,direction,status,reason,quote_volume,provider,entry,stop,tp,score,cur["close"],"STOP",retest_level=level,confirmation=conf)


def analyze_range_reversion(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    h4 = _closed(h4_rows, 70)
    if len(h4) < 70:
        return {"strategy":"range_mean_reversion","symbol":symbol,"status":"NO_SETUP","reason":"Недостаточно H4 данных"}
    work=h4[-60:]; cur=work[-1]; closes=[x["close"] for x in work]; a=atr(work,14)
    upper=max(x["high"] for x in work[:-4]); lower=min(x["low"] for x in work[:-4]); mid=(upper+lower)/2
    width=upper-lower
    if width<=0:
        return {"strategy":"range_mean_reversion","symbol":symbol,"status":"NO_SETUP","reason":"Некорректный range"}
    ema_now=_ema(closes,30); ema_old=_ema(closes[:-12],30); slope=abs(ema_now-ema_old)/(a or 1)
    tolerance=max(a*0.35,width*0.04)
    low_touches=sum(1 for x in work[:-4] if x["low"]<=lower+tolerance)
    high_touches=sum(1 for x in work[:-4] if x["high"]>=upper-tolerance)
    stable=slope<=1.2 and low_touches>=2 and high_touches>=2
    if not stable:
        return {"strategy":"range_mean_reversion","symbol":symbol,"status":"NO_SETUP","reason":"H4 range недостаточно устойчив или рынок трендовый"}
    dist_low=(cur["close"]-lower)/width; dist_high=(upper-cur["close"])/width
    direction="LONG" if dist_low<=dist_high else "SHORT"
    conf=_confirmation(work,direction)
    near=(dist_low<=0.18 if direction=="LONG" else dist_high<=0.18)
    confirmed=near and (conf["reject"] or conf["engulf"])
    if direction=="LONG":
        entry=lower+width*0.08; stop=lower-a*0.35; tp=mid
    else:
        entry=upper-width*0.08; stop=upper+a*0.35; tp=mid
    status="READY" if confirmed else ("WATCH" if near else "WAITING")
    reason="Range edge + rejection" if confirmed else ("Цена у границы range" if near else "Range есть, цена пока не у границы")
    score=35+min(20,(low_touches+high_touches)*3)+(20 if near else 0)+(20 if confirmed else 0)+max(0,5-slope*3)
    return _base("range_mean_reversion",symbol,direction,status,reason,quote_volume,provider,entry,stop,tp,score,cur["close"],"LIMIT",
                 entry_zone_low=(lower if direction=="LONG" else upper-tolerance),entry_zone_high=(lower+tolerance if direction=="LONG" else upper),range_low=lower,range_high=upper,range_mid=mid,range_touches=low_touches+high_touches)


def _anchored_vwap(candles: list[dict[str,float]], start: int) -> float:
    pv=0.0; vol=0.0
    for x in candles[start:]:
        v=max(0.0,x["volume"]); typical=(x["high"]+x["low"]+x["close"])/3
        pv+=typical*v; vol+=v
    return pv/vol if vol>0 else 0.0


def analyze_avwap(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    d1,h4=_closed(d1_rows,80),_closed(h4_rows,80)
    if len(d1)<80 or len(h4)<60:
        return {"strategy":"anchored_vwap_pullback","symbol":symbol,"status":"NO_SETUP","reason":"Недостаточно данных"}
    tr=_trend(d1)
    if tr["direction"]=="RANGE":
        return {"strategy":"anchored_vwap_pullback","symbol":symbol,"status":"NO_SETUP","reason":"Нет D1 тренда"}
    direction="LONG" if tr["direction"]=="UP" else "SHORT"; a=atr(h4,14); cur=h4[-1]
    highs,lows=pivots(h4[-80:],2)
    candidates=lows if direction=="LONG" else highs
    if not candidates:
        return {"strategy":"anchored_vwap_pullback","symbol":symbol,"status":"NO_SETUP","reason":"Нет H4 anchor swing"}
    idx,_=candidates[-1]
    av=_anchored_vwap(h4[-80:],idx)
    if av<=0:
        return {"strategy":"anchored_vwap_pullback","symbol":symbol,"status":"NO_SETUP","reason":"AVWAP не рассчитан"}
    distance=abs(cur["close"]/av-1)*100; touched=cur["low"]<=av+a*0.15 and cur["high"]>=av-a*0.15
    conf=_confirmation(h4,direction); confirmed=touched and (conf["engulf"] or conf["bos"] or conf["reject"])
    entry=av
    if direction=="LONG":
        stop=min(x["low"] for x in h4[-12:])-a*0.25; tp=max(x["high"] for x in h4[-40:]); tp=max(tp,entry+2.2*(entry-stop))
    else:
        stop=max(x["high"] for x in h4[-12:])+a*0.25; tp=min(x["low"] for x in h4[-40:]); tp=min(tp,entry-2.2*(stop-entry))
    status="READY" if confirmed else ("WATCH" if touched or distance<=1.5 else "WAITING")
    reason="D1 trend + AVWAP pullback + confirmation" if confirmed else ("Цена у AVWAP" if status=="WATCH" else "Тренд есть, ждём AVWAP pullback")
    score=45+min(15,tr["strength"]*5)+(20 if touched else 0)+(20 if confirmed else 0)
    return _base("anchored_vwap_pullback",symbol,direction,status,reason,quote_volume,provider,entry,stop,tp,score,cur["close"],"LIMIT",anchored_vwap=av,d1_trend=tr["direction"],distance_to_zone_pct=distance)


def _bb(values: list[float], period=20, mult=2.0):
    sample=values[-period:]
    if len(sample)<period:
        return (0.0,0.0,0.0,0.0)
    m=mean(sample); sd=pstdev(sample); upper=m+mult*sd; lower=m-mult*sd; width=(upper-lower)/m if m else 0.0
    return lower,m,upper,width


def analyze_volatility_squeeze(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    h4=_closed(h4_rows,120)
    if len(h4)<100:
        return {"strategy":"volatility_squeeze","symbol":symbol,"status":"NO_SETUP","reason":"Недостаточно H4 данных"}
    closes=[x["close"] for x in h4]; widths=[]
    for i in range(20,len(closes)+1):
        widths.append(_bb(closes[:i],20)[3])
    low,m,up,w=_bb(closes,20); hist=sorted(widths[-80:]); rank=(sum(1 for x in hist if x<=w)/len(hist))*100 if hist else 100
    cur=h4[-1]; avg_vol=_sma([x["volume"] for x in h4[:-1]],20); vol_expand=cur["volume"]>=avg_vol*1.4 if avg_vol>0 else False
    prior_high=max(x["high"] for x in h4[-21:-1]); prior_low=min(x["low"] for x in h4[-21:-1])
    long_break=cur["close"]>prior_high and vol_expand; short_break=cur["close"]<prior_low and vol_expand
    squeeze=rank<=25 or widths[-2]<=sorted(widths[-80:])[max(0,int(len(hist)*0.25)-1)]
    if long_break: direction="LONG"
    elif short_break: direction="SHORT"
    else:
        direction="LONG" if cur["close"]>=m else "SHORT"
    if not squeeze and not (long_break or short_break):
        return {"strategy":"volatility_squeeze","symbol":symbol,"status":"NO_SETUP","reason":"Нет volatility compression"}
    a=atr(h4,14)
    if direction=="LONG": entry=max(cur["high"],prior_high)+a*0.03; stop=min(prior_low,m)-a*0.10; tp=entry+2.5*(entry-stop)
    else: entry=min(cur["low"],prior_low)-a*0.03; stop=max(prior_high,m)+a*0.10; tp=entry-2.5*(stop-entry)
    ready=(long_break or short_break) and squeeze
    status="READY" if ready else "WATCH"
    reason="Squeeze + volume expansion breakout" if ready else "Volatility squeeze активен, ждём breakout"
    score=50+max(0,25-rank)*0.8+(15 if vol_expand else 0)+(20 if ready else 0)
    return _base("volatility_squeeze",symbol,direction,status,reason,quote_volume,provider,entry,stop,tp,score,cur["close"],"STOP",bb_width=w,bb_percentile=rank,volume_expansion=vol_expand)


def analyze_donchian(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    d1,h4=_closed(d1_rows,80),_closed(h4_rows,60)
    if len(d1)<70 or len(h4)<35:
        return {"strategy":"donchian_trend","symbol":symbol,"status":"NO_SETUP","reason":"Недостаточно данных"}
    tr=_trend(d1); cur=h4[-1]; prior=h4[-21:-1]
    high20=max(x["high"] for x in prior); low20=min(x["low"] for x in prior); a=atr(h4,14)
    if tr["direction"]=="UP": direction="LONG"; level=high20; ready=cur["close"]>high20
    elif tr["direction"]=="DOWN": direction="SHORT"; level=low20; ready=cur["close"]<low20
    else:
        return {"strategy":"donchian_trend","symbol":symbol,"status":"NO_SETUP","reason":"Нет D1 тренда"}
    distance=abs(cur["close"]/level-1)*100 if level else 999
    if direction=="LONG": entry=high20+a*0.03; stop=min(x["low"] for x in h4[-10:])-a*0.15; tp=entry+3.0*(entry-stop)
    else: entry=low20-a*0.03; stop=max(x["high"] for x in h4[-10:])+a*0.15; tp=entry-3.0*(stop-entry)
    status="READY" if ready else ("WATCH" if distance<=1.0 else "WAITING")
    reason="Donchian breakout по D1 тренду" if ready else ("Цена у Donchian trigger" if status=="WATCH" else "D1 trend есть, ждём channel breakout")
    score=45+min(15,tr["strength"]*5)+(20 if distance<=1 else 0)+(20 if ready else 0)
    return _base("donchian_trend",symbol,direction,status,reason,quote_volume,provider,entry,stop,tp,score,cur["close"],"STOP",donchian_high=high20,donchian_low=low20,d1_trend=tr["direction"])


def _extract_funding(value) -> float | None:
    if value is None: return None
    if isinstance(value, dict):
        for k in ("lastFundingRate","fundingRate","funding_rate","funding"):
            if k in value and value[k] not in (None,""):
                try: return float(value[k])
                except Exception: pass
    try: return float(value)
    except Exception: return None


def _extract_oi_series(value) -> list[float]:
    if not isinstance(value,list): return []
    out=[]
    keys=("sumOpenInterestValue","sumOpenInterest","openInterest","open_interest","oi","value")
    for row in value:
        if isinstance(row,dict):
            found=None
            for k in keys:
                if k in row and row[k] not in (None,""):
                    try: found=float(row[k]); break
                    except Exception: pass
            if found is not None: out.append(found)
        else:
            try: out.append(float(row))
            except Exception: pass
    return out


def _oi_change_pct(derivatives: dict[str,Any]) -> float | None:
    series=_extract_oi_series((derivatives or {}).get("oi_history"))
    if len(series)<2 or series[0]==0: return None
    return (series[-1]/series[0]-1)*100


def analyze_funding_oi(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    h4=_closed(h4_rows,60)
    if len(h4)<35:
        return {"strategy":"funding_oi_squeeze","symbol":symbol,"status":"NO_SETUP","reason":"Недостаточно H4 данных"}
    funding=_extract_funding((derivatives or {}).get("premium")); oi_change=_oi_change_pct(derivatives or {})
    if funding is None or oi_change is None:
        return {"strategy":"funding_oi_squeeze","symbol":symbol,"status":"NO_SETUP","reason":"Funding/OI недоступны — synthetic zero не используется"}
    cur=h4[-1]; a=atr(h4,14); recent=h4[-12:-1]; prev_high=max(x["high"] for x in recent); prev_low=min(x["low"] for x in recent)
    long_pressure=funding<=-0.0003 and oi_change>=3.0
    short_pressure=funding>=0.0003 and oi_change>=3.0
    long_trigger=cur["close"]>prev_high; short_trigger=cur["close"]<prev_low
    if long_pressure: direction="LONG"
    elif short_pressure: direction="SHORT"
    else:
        return {"strategy":"funding_oi_squeeze","symbol":symbol,"status":"NO_SETUP","reason":"Нет экстремального funding + роста OI"}
    ready=(direction=="LONG" and long_trigger) or (direction=="SHORT" and short_trigger)
    if direction=="LONG": entry=max(cur["high"],prev_high)+a*0.03; stop=min(x["low"] for x in h4[-8:])-a*0.15; tp=entry+2.5*(entry-stop)
    else: entry=min(cur["low"],prev_low)-a*0.03; stop=max(x["high"] for x in h4[-8:])+a*0.15; tp=entry-2.5*(stop-entry)
    status="READY" if ready else "WATCH"; reason="Funding/OI pressure + H4 squeeze trigger" if ready else "Funding/OI pressure есть, ждём price trigger"
    extreme=min(20,abs(funding)*10000*4); score=45+min(20,oi_change)+extreme+(20 if ready else 0)
    return _base("funding_oi_squeeze",symbol,direction,status,reason,quote_volume,provider,entry,stop,tp,score,cur["close"],"STOP",funding_rate=funding,oi_change_pct=oi_change)


def analyze_oi_divergence(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    h4=_closed(h4_rows,70); oi_change=_oi_change_pct(derivatives or {})
    if len(h4)<50 or oi_change is None:
        return {"strategy":"oi_price_divergence","symbol":symbol,"status":"NO_SETUP","reason":"Недостаточно H4/OI данных"}
    cur=h4[-1]; a=atr(h4,14); prior=h4[-45:-5]
    price_high=max(x["high"] for x in prior); price_low=min(x["low"] for x in prior)
    new_high=cur["high"]>price_high; new_low=cur["low"]<price_low
    weak_oi=oi_change<=1.0
    if new_high and weak_oi: direction="SHORT"; extreme=cur["high"]
    elif new_low and weak_oi: direction="LONG"; extreme=cur["low"]
    else:
        return {"strategy":"oi_price_divergence","symbol":symbol,"status":"NO_SETUP","reason":"Нет price extreme / weak-OI divergence"}
    conf=_confirmation(h4,direction); confirmed=conf["engulf"] or conf["bos"] or conf["reject"]
    mid=(price_high+price_low)/2
    if direction=="LONG": entry=cur["high"]+a*0.03; stop=extreme-a*0.2; tp=max(mid,entry+2.0*(entry-stop))
    else: entry=cur["low"]-a*0.03; stop=extreme+a*0.2; tp=min(mid,entry-2.0*(stop-entry))
    status="READY" if confirmed else "WATCH"; reason="Price/OI divergence + H4 reversal confirmation" if confirmed else "Divergence есть, ждём H4 reversal"
    score=55+min(20,max(0,-oi_change)*2)+(20 if confirmed else 0)
    return _base("oi_price_divergence",symbol,direction,status,reason,quote_volume,provider,entry,stop,tp,score,cur["close"],"STOP",oi_change_pct=oi_change,price_extreme=extreme,confirmation=conf)


def analyze_rsi_divergence(symbol, quote_volume, d1_rows, h4_rows, provider=None, derivatives=None):
    h4=_closed(h4_rows,100)
    if len(h4)<70:
        return {"strategy":"rsi_divergence_structure","symbol":symbol,"status":"NO_SETUP","reason":"Недостаточно H4 данных"}
    rsi=_rsi_series(h4,14); highs,lows=pivots(h4,2); direction=None; p1=p2=None; r1=r2=None
    if len(lows)>=2:
        i1,v1=lows[-2]; i2,v2=lows[-1]
        if rsi[i1] is not None and rsi[i2] is not None and v2<v1 and rsi[i2]>rsi[i1]+2:
            direction="LONG"; p1,p2=v1,v2; r1,r2=rsi[i1],rsi[i2]
    if direction is None and len(highs)>=2:
        i1,v1=highs[-2]; i2,v2=highs[-1]
        if rsi[i1] is not None and rsi[i2] is not None and v2>v1 and rsi[i2]<rsi[i1]-2:
            direction="SHORT"; p1,p2=v1,v2; r1,r2=rsi[i1],rsi[i2]
    if direction is None:
        return {"strategy":"rsi_divergence_structure","symbol":symbol,"status":"NO_SETUP","reason":"Нет подтверждённой RSI divergence"}
    cur=h4[-1]; a=atr(h4,14); conf=_confirmation(h4,direction); confirmed=conf["engulf"] or conf["bos"] or conf["reject"]
    if direction=="LONG": entry=cur["high"]+a*0.03; stop=p2-a*0.2; local=max(x["high"] for x in h4[-35:]); tp=max(local,entry+2.2*(entry-stop))
    else: entry=cur["low"]-a*0.03; stop=p2+a*0.2; local=min(x["low"] for x in h4[-35:]); tp=min(local,entry-2.2*(stop-entry))
    status="READY" if confirmed else "WATCH"; reason="RSI divergence + H4 structure confirmation" if confirmed else "RSI divergence есть, ждём structure confirmation"
    score=55+min(20,abs((r2 or 0)-(r1 or 0))*2)+(20 if confirmed else 0)
    return _base("rsi_divergence_structure",symbol,direction,status,reason,quote_volume,provider,entry,stop,tp,score,cur["close"],"STOP",rsi_first=r1,rsi_second=r2,pivot_first=p1,pivot_second=p2,confirmation=conf)


ANALYZERS = {
    "ma_ribbon_cross": analyze_ma_ribbon_cross,
    "ma55_cycle": analyze_ma55_cycle,
    "smart_money_confluence": analyze_smart_money_confluence,
    "liquidity_sweep_reclaim": analyze_liquidity_sweep,
    "ema_trend_pullback": analyze_ema_pullback,
    "breakout_retest": analyze_breakout_retest,
    "range_mean_reversion": analyze_range_reversion,
    "anchored_vwap_pullback": analyze_avwap,
    "volatility_squeeze": analyze_volatility_squeeze,
    "donchian_trend": analyze_donchian,
    "funding_oi_squeeze": analyze_funding_oi,
    "oi_price_divergence": analyze_oi_divergence,
    "rsi_divergence_structure": analyze_rsi_divergence,
}


def analyze_strategy(strategy: str, symbol: str, quote_volume: float, d1_rows, h4_rows, provider: str | None = None, derivatives: dict[str,Any] | None = None):
    if strategy == "fib_05_pullback":
        result = analyze_fib_symbol(symbol, quote_volume, d1_rows, h4_rows, provider)
        if result.get("entry_price"):
            result.setdefault("direction", "LONG")
            result.setdefault("entry_mode", "LIMIT")
        return result
    analyzer = ANALYZERS.get(strategy)
    if analyzer is None:
        raise KeyError(strategy)
    return analyzer(symbol, quote_volume, d1_rows, h4_rows, provider, derivatives or {})
