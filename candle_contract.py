"""Shared Binance-shaped OHLCV candle normalization.

Contract:
  [0] open time ms (inclusive)
  [1] open
  [2] high
  [3] low
  [4] close
  [5] base volume
  [6] logical close time ms (exclusive end of interval)
  [7] quote volume / turnover
  [8] trade count
  [9] taker buy base volume
  [10] taker buy quote volume
  [11] ignored/reserved

All exchange adapters must use this contract so event-time consumers can safely
filter forming candles and historical horizons.
"""
from __future__ import annotations

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def interval_ms(interval: str) -> int:
    key = str(interval or "").strip()
    if key not in INTERVAL_MS:
        raise ValueError(f"Unsupported candle interval: {interval!r}")
    return INTERVAL_MS[key]


def candle_end_ms(open_time_ms: int | float | str, interval: str) -> int:
    return int(float(open_time_ms)) + interval_ms(interval)


def normalize_candle(
    open_time_ms,
    open_price,
    high,
    low,
    close,
    base_volume=0,
    *,
    interval: str,
    quote_volume=0,
    trade_count=0,
    taker_buy_base=0,
    taker_buy_quote=0,
    close_time_ms=None,
):
    start = int(float(open_time_ms))
    logical_end = candle_end_ms(start, interval)
    if close_time_ms is not None:
        try:
            supplied = int(float(close_time_ms))
            # Some APIs return the open timestamp in a close-time-looking field.
            # Never accept an end <= start; also clamp exotic timestamps to the
            # requested interval to keep the cross-exchange contract deterministic.
            if start < supplied <= logical_end:
                logical_end = supplied
        except Exception:
            pass
    return [
        start,
        open_price,
        high,
        low,
        close,
        base_volume,
        logical_end,
        quote_volume,
        int(trade_count or 0),
        taker_buy_base or 0,
        taker_buy_quote or 0,
        "0",
    ]


def normalize_existing_rows(rows, interval: str):
    """Validate/repair Binance-shaped rows returned by an adapter.

    This is the final boundary at the multi-exchange abstraction: even if an
    adapter accidentally exposes open_time as row[6], downstream event-time
    code receives a deterministic logical candle end. Malformed rows are
    rejected instead of silently poisoning execution/outcome logic.
    """
    out = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise ValueError(f"Malformed candle row: {row!r}")
        vals = list(row[:12]) + [0] * max(0, 12 - len(row))
        start = int(float(vals[0]))
        if start <= 0:
            raise ValueError(f"Invalid candle open time: {vals[0]!r}")
        vals[0] = start
        vals[6] = candle_end_ms(start, interval)
        # OHLC must be numeric and internally sane. Keep original value types
        # for analyzer compatibility, but validate them here.
        opn, high, low, close = map(float, vals[1:5])
        if high < low or high < max(opn, close) or low > min(opn, close):
            raise ValueError(f"Invalid OHLC candle: {row!r}")
        out.append(vals[:12])
    out.sort(key=lambda r: int(r[0]))
    return out
