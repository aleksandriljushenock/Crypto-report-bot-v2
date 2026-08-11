from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any

from strategies.fib_pullback import analyze_symbol as analyze_fib_symbol, atr, normalize_klines, pivots


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
