from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_rows(rows: list, minutes: int):
    out=[]
    for item in rows or []:
        try:
            start=datetime.fromtimestamp(float(item[0])/1000.0,tz=timezone.utc)
            if len(item)>6 and item[6] is not None:
                end=datetime.fromtimestamp(float(item[6])/1000.0,tz=timezone.utc)
            else:
                end=start+timedelta(minutes=minutes)
            close=float(item[4]); high=float(item[2]); low=float(item[3])
            out.append((start,end,close,high,low))
        except Exception:
            continue
    return sorted(out,key=lambda x:x[0])


def historical_price_at(client: Any, symbol: str, target: datetime, *, now: datetime | None=None, max_bars: int=1000) -> float | None:
    """Return a price known at or before target without look-ahead.

    Chooses the finest interval whose max_bars history should reach the target.
    Uses the close of the latest fully completed candle and rejects stale gaps.
    """
    if target.tzinfo is None:
        target=target.replace(tzinfo=timezone.utc)
    now=now or datetime.now(timezone.utc)
    age_minutes=max(0.0,(now-target).total_seconds()/60.0)
    choices=(("1m",1),("5m",5),("1h",60),("4h",240),("1d",1440))
    interval,minutes=choices[-1]
    for iv,m in choices:
        if age_minutes+m <= max_bars*m:
            interval,minutes=iv,m
            break
    rows=client.klines(str(symbol).upper(),interval,max_bars) or []
    candles=_parse_rows(rows,minutes)
    eligible=[row for row in candles if row[1] <= target]
    if not eligible:
        return None
    latest=eligible[-1]
    bar=latest[1]-latest[0]
    if target-latest[1] > max(bar*2,timedelta(minutes=2)):
        return None
    return latest[2]


def market_price_now(client: Any, symbol: str) -> float | None:
    try:
        t=client.ticker_24h(str(symbol).upper()) or {}
        for key in ("markPrice","lastPrice","price","close"):
            v=t.get(key)
            if v is not None and float(v)>0:
                return float(v)
    except Exception:
        return None
    return None


def historical_candles_between(client: Any, symbol: str, start: datetime, end: datetime, *, now: datetime | None=None, max_bars: int=1000):
    """Fetch completed candles covering an old event-time window without relying on latest 5m only.

    Returns (candles, interval, minutes, coverage_start, coverage_end). Each candle is
    (start,end,close,high,low). The finest interval that can still reach the requested
    window from *now* is selected.
    """
    if start.tzinfo is None: start=start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None: end=end.replace(tzinfo=timezone.utc)
    now=now or datetime.now(timezone.utc)
    choices=(("1m",1),("5m",5),("1h",60),("4h",240),("1d",1440))
    age_minutes=max(1.0,(now-start).total_seconds()/60.0)
    span_minutes=max(1.0,(end-start).total_seconds()/60.0)
    interval,minutes=choices[-1]
    for iv,m in choices:
        if max(age_minutes,span_minutes)+m <= max_bars*m:
            interval,minutes=iv,m; break
    rows=client.klines(str(symbol).upper(),interval,max_bars) or []
    parsed=_parse_rows(rows,minutes)
    completed=[c for c in parsed if c[1] <= now]
    selected=[c for c in completed if c[1] > start and c[0] < end]
    coverage_start=completed[0][0] if completed else None
    coverage_end=completed[-1][1] if completed else None
    return selected,interval,minutes,coverage_start,coverage_end
