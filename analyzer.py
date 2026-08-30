from smc_analyzer import analyze_multiple_timeframes
from order_block_analyzer import (
    analyze_order_blocks_multiple_timeframes,
)
from fvg_analyzer import analyze_fvg_multiple_timeframes
from derivatives_analyzer import (
    calculate_oi_analysis,
    calculate_funding_analysis,
)
from relative_strength import calculate_relative_strength
from brief_builder import build_brief_report
from rule_engine import evaluate_rules
from gpt_analyzer import analyze_with_gpt
from report_builder import build_pretty_report
import json
import sys
from pathlib import Path
from statistics import mean
from indicators import ema, rsi, vwap


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_snapshot(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def kline_to_dict(k):
    return {
        "open_time": k[0],
        "open": to_float(k[1]),
        "high": to_float(k[2]),
        "low": to_float(k[3]),
        "close": to_float(k[4]),
        "volume": to_float(k[5]),
        "quote_volume": to_float(k[7]),
        "trades": int(k[8]),
        "taker_buy_volume": to_float(k[9]),
        "taker_buy_quote_volume": to_float(k[10]),
    }


def parse_klines(raw):
    if isinstance(raw, dict) and "error" in raw:
        return []
    return [kline_to_dict(k) for k in raw]


def atr_percent(candles, period=14):
    if len(candles) < period + 1:
        return 0.0

    trs = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        trs.append(tr)

    atr = mean(trs[-period:])
    price = candles[-1]["close"]
    return (atr / price) * 100 if price else 0.0


def relative_volume(candles, lookback=20):
    if len(candles) < lookback + 1:
        return 1.0

    recent = candles[-1]["quote_volume"]
    avg = mean([c["quote_volume"] for c in candles[-lookback - 1:-1]])

    return recent / avg if avg else 1.0


def trend_score(candles):
    if len(candles) < 50:
        return 0

    close = candles[-1]["close"]
    ma20 = mean([c["close"] for c in candles[-20:]])
    ma50 = mean([c["close"] for c in candles[-50:]])

    score = 0

    if close > ma20:
        score += 1
    if ma20 > ma50:
        score += 1
    if candles[-1]["close"] > candles[-5]["close"]:
        score += 1
    if candles[-1]["low"] > candles[-10]["low"]:
        score += 1

    return score


def structure_label(candles):
    if len(candles) < 20:
        return "UNKNOWN"

    last = candles[-1]
    prev_5_high = max(c["high"] for c in candles[-6:-1])
    prev_5_low = min(c["low"] for c in candles[-6:-1])
    prev_20_high = max(c["high"] for c in candles[-21:-1])
    prev_20_low = min(c["low"] for c in candles[-21:-1])

    if last["close"] > prev_20_high:
        return "BOS_UP"
    if last["close"] < prev_20_low:
        return "BOS_DOWN"
    if last["high"] > prev_5_high and last["close"] < prev_5_high:
        return "SWEEP_HIGH"
    if last["low"] < prev_5_low and last["close"] > prev_5_low:
        return "SWEEP_LOW"

    return "RANGE"


def orderbook_imbalance(depth):
    if not isinstance(depth, dict) or "error" in depth:
        return 0.0

    bids = depth.get("bids", [])
    asks = depth.get("asks", [])

    bid_notional = sum(to_float(price) * to_float(qty) for price, qty in bids[:20])
    ask_notional = sum(to_float(price) * to_float(qty) for price, qty in asks[:20])

    total = bid_notional + ask_notional
    if total == 0:
        return 0.0

    return (bid_notional - ask_notional) / total


def funding_value(premium):
    if not isinstance(premium, dict) or "error" in premium:
        return 0.0
    return to_float(premium.get("lastFundingRate")) * 100


def taker_ratio(data):
    if not isinstance(data, list) or not data:
        return 1.0

    last = data[-1]

    buy = to_float(last.get("buyVol"))
    sell = to_float(last.get("sellVol"))

    if sell == 0:
        return 1.0

    return buy / sell


def long_short_ratio(data):
    if not isinstance(data, list) or not data:
        return 1.0

    last = data[-1]
    return to_float(last.get("longShortRatio"), 1.0)


def calculate_score(symbol_data):
    ticker = symbol_data.get("ticker24h", {})
    klines = symbol_data.get("klines", {})

    c15 = parse_klines(klines.get("15m", []))
    c1h = parse_klines(klines.get("1h", []))
    c4h = parse_klines(klines.get("4h", []))

    price_change = to_float(ticker.get("priceChangePercent"))
    quote_volume = to_float(ticker.get("quoteVolume"))

    closes_15m = [c["close"] for c in c15]
    closes_1h = [c["close"] for c in c1h]
    closes_4h = [c["close"] for c in c4h]

    last_price = closes_15m[-1] if closes_15m else 0

    atr_15m = atr_percent(c15)
    atr_1h = atr_percent(c1h)
    rv_15m = relative_volume(c15)
    rv_1h = relative_volume(c1h)

    trend_15m = trend_score(c15)
    trend_1h = trend_score(c1h)
    trend_4h = trend_score(c4h)

    struct_15m = structure_label(c15)
    struct_1h = structure_label(c1h)

    ema20_1h = ema(closes_1h, 20)
    ema50_1h = ema(closes_1h, 50)
    ema200_1h = ema(closes_1h, 200)

    rsi_1h = rsi(closes_1h, 14)
    vwap_15m = vwap(c15[-96:]) if len(c15) >= 20 else None

    funding = funding_value(symbol_data.get("premiumIndex", {}))
    ob_imbalance = orderbook_imbalance(symbol_data.get("depth", {}))
    taker = taker_ratio(symbol_data.get("takerBuySellVolume", []))
    ls_ratio = long_short_ratio(symbol_data.get("longShortRatio", []))

    score = 0

    if quote_volume >= 100_000_000:
        score += 15
    elif quote_volume >= 50_000_000:
        score += 8

    if atr_1h >= 1.0:
        score += 10
    elif atr_1h >= 0.5:
        score += 5

    score += trend_15m * 2
    score += trend_1h * 3
    score += trend_4h * 4

    if ema20_1h and ema50_1h and last_price > ema20_1h > ema50_1h:
        score += 10
    elif ema20_1h and last_price > ema20_1h:
        score += 5

    if ema200_1h and last_price > ema200_1h:
        score += 5

    if vwap_15m and last_price > vwap_15m:
        score += 5

    if rsi_1h:
        if 45 <= rsi_1h <= 65:
            score += 5
        elif rsi_1h > 75:
            score -= 5
        elif rsi_1h < 35:
            score -= 3

    if struct_15m == "BOS_UP":
        score += 10
    elif struct_15m == "SWEEP_LOW":
        score += 8
    elif struct_15m == "RANGE":
        score += 2
    elif struct_15m == "BOS_DOWN":
        score -= 10

    if struct_1h == "BOS_UP":
        score += 15
    elif struct_1h == "SWEEP_LOW":
        score += 10
    elif struct_1h == "RANGE":
        score += 3
    elif struct_1h == "BOS_DOWN":
        score -= 15

    if rv_15m >= 1.5:
        score += 8
    elif rv_15m >= 1.1:
        score += 4

    if rv_1h >= 1.5:
        score += 8
    elif rv_1h >= 1.1:
        score += 4

    if -0.02 <= funding <= 0.02:
        score += 5
    elif funding > 0.05:
        score -= 5

    if ob_imbalance > 0.10:
        score += 5
    elif ob_imbalance < -0.10:
        score -= 5

    if taker > 1.1:
        score += 5
    elif taker < 0.9:
        score -= 5

    if 0.8 <= ls_ratio <= 1.4:
        score += 3
    elif ls_ratio > 2:
        score -= 5

    score = max(0, min(100, score))

    direction = "NO_TRADE"

    bullish_conditions = [
        struct_1h in ["BOS_UP", "SWEEP_LOW", "RANGE"],
        trend_1h >= 2,
        ema20_1h is not None and last_price > ema20_1h,
        vwap_15m is not None and last_price > vwap_15m,
        taker >= 0.95,
    ]

    bearish_conditions = [
        struct_1h in ["BOS_DOWN", "SWEEP_HIGH", "RANGE"],
        trend_1h <= 1,
        ema20_1h is not None and last_price < ema20_1h,
        vwap_15m is not None and last_price < vwap_15m,
        taker <= 0.95,
    ]

    if sum(bullish_conditions) >= 4:
        direction = "LONG_BIAS"
    elif sum(bearish_conditions) >= 4:
        direction = "SHORT_BIAS"

    # V52: the old scalar score rewarded bullish structure and penalized bearish
    # structure before direction was known, making SHORT candidates structurally
    # unable to reach the same gate. Re-score technical evidence symmetrically.
    if direction != "NO_TRADE":
        directional = 0.0
        directional += 15 if quote_volume >= 100_000_000 else (8 if quote_volume >= 50_000_000 else 0)
        directional += 10 if atr_1h >= 1.0 else (5 if atr_1h >= 0.5 else 0)
        directional += 8 if rv_15m >= 1.5 else (4 if rv_15m >= 1.1 else 0)
        directional += 8 if rv_1h >= 1.5 else (4 if rv_1h >= 1.1 else 0)
        directional += 5 if -0.02 <= funding <= 0.02 else 0
        directional += 3 if 0.8 <= ls_ratio <= 1.4 else 0
        if direction == "LONG_BIAS":
            directional += trend_15m * 2 + trend_1h * 3 + trend_4h * 4
            directional += 10 if (ema20_1h and ema50_1h and last_price > ema20_1h > ema50_1h) else (5 if ema20_1h and last_price > ema20_1h else 0)
            directional += 5 if ema200_1h and last_price > ema200_1h else 0
            directional += 5 if vwap_15m and last_price > vwap_15m else 0
            directional += 10 if struct_15m == "BOS_UP" else (8 if struct_15m == "SWEEP_LOW" else (2 if struct_15m == "RANGE" else 0))
            directional += 15 if struct_1h == "BOS_UP" else (10 if struct_1h == "SWEEP_LOW" else (3 if struct_1h == "RANGE" else 0))
            directional += 5 if ob_imbalance > 0.10 else 0
            directional += 5 if taker > 1.05 else 0
            directional += 5 if rsi_1h is not None and 45 <= rsi_1h <= 65 else 0
        else:
            directional += (4-trend_15m) * 2 + (4-trend_1h) * 3 + (4-trend_4h) * 4
            directional += 10 if (ema20_1h and ema50_1h and last_price < ema20_1h < ema50_1h) else (5 if ema20_1h and last_price < ema20_1h else 0)
            directional += 5 if ema200_1h and last_price < ema200_1h else 0
            directional += 5 if vwap_15m and last_price < vwap_15m else 0
            directional += 10 if struct_15m == "BOS_DOWN" else (8 if struct_15m == "SWEEP_HIGH" else (2 if struct_15m == "RANGE" else 0))
            directional += 15 if struct_1h == "BOS_DOWN" else (10 if struct_1h == "SWEEP_HIGH" else (3 if struct_1h == "RANGE" else 0))
            directional += 5 if ob_imbalance < -0.10 else 0
            directional += 5 if taker < 0.95 else 0
            directional += 5 if rsi_1h is not None and 35 <= rsi_1h <= 55 else 0
        score = max(0, min(100, directional))

    if score >= 75 and direction != "NO_TRADE":
        status = "TRADE_CANDIDATE"
    elif score >= 60:
        status = "WATCH"
    else:
        status = "SKIP"

    return {
        "symbol": symbol_data.get("symbol"),
        "score": score,
        "status": status,
        "direction": direction,
        "priceChange24h": price_change,
        "quoteVolume": quote_volume,
        "atr15mPercent": round(atr_15m, 3),
        "atr1hPercent": round(atr_1h, 3),
        "relativeVolume15m": round(rv_15m, 2),
        "relativeVolume1h": round(rv_1h, 2),
        "structure15m": struct_15m,
        "structure1h": struct_1h,
        "fundingPercent": round(funding, 4),
        "orderbookImbalance": round(ob_imbalance, 3),
        "takerBuySellRatio": round(taker, 3),
        "longShortRatio": round(ls_ratio, 3),
        "ema20_1h": round(ema20_1h, 6) if ema20_1h else None,
        "ema50_1h": round(ema50_1h, 6) if ema50_1h else None,
        "ema200_1h": round(ema200_1h, 6) if ema200_1h else None,
        "rsi1h": round(rsi_1h, 2) if rsi_1h else None,
        "vwap15m": round(vwap_15m, 6) if vwap_15m else None,
    }


def build_trade_levels(symbol_data, direction="LONG_BIAS"):
    direction = str(direction or "LONG_BIAS").upper()
    klines = symbol_data.get("klines", {})
    c15 = parse_klines(klines.get("15m", []))
    c1h = parse_klines(klines.get("1h", []))

    if len(c15) < 50 or len(c1h) < 50:
        return None

    last_price = c15[-1]["close"]

    resistance_15m = max(c["high"] for c in c15[-20:])
    support_15m = min(c["low"] for c in c15[-20:])

    resistance_1h = max(c["high"] for c in c1h[-24:])
    support_1h = min(c["low"] for c in c1h[-24:])

    atr_15m_value = atr_percent(c15) / 100 * last_price
    atr_1h_value = atr_percent(c1h) / 100 * last_price

    buffer = max(atr_15m_value * 0.25, last_price * 0.001)

    if direction in {"SHORT", "SHORT_BIAS", "SELL"}:
        # Mirror the geometry for SHORT: break support, stop above, targets below;
        # pullbacks are sold from resistance instead of bought from support.
        breakout_entry = support_15m - buffer
        breakout_stop = support_15m + atr_15m_value * 1.2
        pullback_entry_low = resistance_15m - atr_15m_value * 0.8
        pullback_entry_high = resistance_15m - buffer
        pullback_stop = resistance_15m + atr_15m_value * 1.2
        tp1 = breakout_entry - atr_1h_value * 1.0
        tp2 = breakout_entry - atr_1h_value * 2.0
        tp3 = breakout_entry - atr_1h_value * 3.0
        risk_breakout = breakout_stop - breakout_entry
        reward_breakout = breakout_entry - tp2
        pullback_entry_mid = (pullback_entry_low + pullback_entry_high) / 2
        risk_pullback = pullback_stop - pullback_entry_mid
        reward_pullback = pullback_entry_mid - support_1h
    else:
        breakout_entry = resistance_15m + buffer
        breakout_stop = resistance_15m - atr_15m_value * 1.2
        pullback_entry_low = support_15m + buffer
        pullback_entry_high = support_15m + atr_15m_value * 0.8
        pullback_stop = support_15m - atr_15m_value * 1.2
        tp1 = breakout_entry + atr_1h_value * 1.0
        tp2 = breakout_entry + atr_1h_value * 2.0
        tp3 = breakout_entry + atr_1h_value * 3.0
        risk_breakout = breakout_entry - breakout_stop
        reward_breakout = tp2 - breakout_entry
        pullback_entry_mid = (pullback_entry_low + pullback_entry_high) / 2
        risk_pullback = pullback_entry_mid - pullback_stop
        reward_pullback = resistance_1h - pullback_entry_mid

    rr_breakout = round(reward_breakout / risk_breakout, 2) if risk_breakout > 0 and reward_breakout > 0 else None
    rr_pullback = round(reward_pullback / risk_pullback, 2) if risk_pullback > 0 and reward_pullback > 0 else None

    return {
        "lastPrice": round(last_price, 6),

        "breakoutEntry": round(breakout_entry, 6),
        "breakoutStop": round(breakout_stop, 6),
        "breakoutRR": rr_breakout,

        "pullbackEntryZone": [
            round(pullback_entry_low, 6),
            round(pullback_entry_high, 6),
        ],
        "pullbackStop": round(pullback_stop, 6),
        "pullbackRR": rr_pullback,

        "tp1": round(tp1, 6),
        "tp2": round(tp2, 6),
        "tp3": round(tp3, 6),

        "resistance15m": round(resistance_15m, 6),
        "support15m": round(support_15m, 6),
        "resistance1h": round(resistance_1h, 6),
        "support1h": round(support_1h, 6),
    }

def make_report(snapshot):
    rows = []

    symbols_data = snapshot.get("symbolsData", {})
    btc_data = symbols_data.get("BTCUSDT")

    if btc_data and "error" not in btc_data:
        btc_data["parsedKlines"] = {
            "15m": parse_klines(
                btc_data.get("klines", {}).get("15m", [])
            ),
            "1h": parse_klines(
                btc_data.get("klines", {}).get("1h", [])
            ),
            "4h": parse_klines(
                btc_data.get("klines", {}).get("4h", [])
            ),
        }

    for symbol, symbol_data in symbols_data.items():
        if "error" in symbol_data:
            continue

        score = calculate_score(symbol_data)
        levels = build_trade_levels(symbol_data, score.get("direction"))

        symbol_data["parsedKlines"] = {
            "15m": parse_klines(
                symbol_data.get("klines", {}).get("15m", [])
            ),
            "1h": parse_klines(
                symbol_data.get("klines", {}).get("1h", [])
            ),
            "4h": parse_klines(
                symbol_data.get("klines", {}).get("4h", [])
            ),
        }

        smc_analysis = analyze_multiple_timeframes(
            symbol_data["parsedKlines"]
        )
        score["smcAnalysis"] = smc_analysis

        order_blocks = analyze_order_blocks_multiple_timeframes(
            symbol_data["parsedKlines"]
        )
        score["orderBlockAnalysis"] = order_blocks

        fvg_analysis = analyze_fvg_multiple_timeframes(
            symbol_data["parsedKlines"]
        )
        score["fvgAnalysis"] = fvg_analysis

        relative_strength = calculate_relative_strength(
            symbol_data,
            btc_data,
        )
        score["relativeStrength"] = relative_strength

        oi_analysis = calculate_oi_analysis(symbol_data)
        score["oiAnalysis"] = oi_analysis

        funding_analysis = calculate_funding_analysis(symbol_data)
        score["fundingAnalysis"] = funding_analysis

        rules = evaluate_rules(score, levels)

        rows.append({
            "score": score,
            "levels": levels,
            "rules": rules,
        })

    rows.sort(
        key=lambda item: item["score"]["score"],
        reverse=True,
    )

    gpt_candidates = [
        row
        for row in rows
        if row["rules"]["finalStatus"]
        in ["TRADE_CANDIDATE", "WATCH"]
    ][:3]

    if not gpt_candidates:
        return build_brief_report(
            rows,
            snapshot.get("runTimeUtc"),
            "Нет кандидатов для AI-разбора.",
        )

    compact_data = {
        "instruction": (
            "Дай очень краткий вывод для Telegram, максимум 8 строк. "
            "Используй SMC: HH, HL, LH, LL, BOS, CHOCH, "
            "liquidity sweep и equal highs/lows. "
            "Учитывай Relative Strength к BTC, Open Interest, "
            "Funding, объем и R/R. "
            "Не рекомендуй вход, если структура не подтверждена "
            "или R/R ниже 1:2. "
            "Укажи лучшее действие, лучший актив, триггер входа "
            "и условие отмены. Не выдумывай данные."
            "Используй Order Blocks и Fair Value Gaps только как зоны, "
            "а не как самостоятельный сигнал. "
            "Не рекомендуй вход только потому, что цена рядом с OB/FVG. "
            "Требуй подтверждение SMC, объема, OI и приемлемого R/R. "
        ),
        "candidates": [],
    }

    for row in gpt_candidates:
        s = row["score"]
        levels = row["levels"]
        rules = row["rules"]

        compact_data["candidates"].append({
            "symbol": s["symbol"],
            "score": s["score"],
            "action": rules.get("action"),
            "bestSetup": rules.get("bestSetup"),
            "mainReason": rules.get("mainReason"),
            "direction": s.get("direction"),

            "legacyStructure": {
                "15m": s.get("structure15m"),
                "1h": s.get("structure1h"),
            },

            "smcAnalysis": s.get("smcAnalysis"),
            "orderBlockAnalysis": s.get(
                "orderBlockAnalysis"
            ),
            "fvgAnalysis": s.get(
                "fvgAnalysis"
            ),
            "relativeStrength": s.get("relativeStrength"),
            "openInterest": s.get("oiAnalysis"),
            "funding": s.get("fundingAnalysis"),

            "marketMetrics": {
                "priceChange24h": s.get("priceChange24h"),
                "quoteVolume": s.get("quoteVolume"),
                "atr15mPercent": s.get("atr15mPercent"),
                "atr1hPercent": s.get("atr1hPercent"),
                "relativeVolume15m": s.get(
                    "relativeVolume15m"
                ),
                "relativeVolume1h": s.get(
                    "relativeVolume1h"
                ),
                "orderbookImbalance": s.get(
                    "orderbookImbalance"
                ),
                "takerBuySellRatio": s.get(
                    "takerBuySellRatio"
                ),
                "longShortRatio": s.get(
                    "longShortRatio"
                ),
            },

            "indicators": {
                "ema20_1h": s.get("ema20_1h"),
                "ema50_1h": s.get("ema50_1h"),
                "ema200_1h": s.get("ema200_1h"),
                "rsi1h": s.get("rsi1h"),
                "vwap15m": s.get("vwap15m"),
            },

            "levels": {
                "lastPrice": (
                    levels.get("lastPrice")
                    if levels
                    else None
                ),
                "breakoutEntry": (
                    levels.get("breakoutEntry")
                    if levels
                    else None
                ),
                "breakoutStop": (
                    levels.get("breakoutStop")
                    if levels
                    else None
                ),
                "breakoutRR": (
                    levels.get("breakoutRR")
                    if levels
                    else None
                ),
                "pullbackEntryZone": (
                    levels.get("pullbackEntryZone")
                    if levels
                    else None
                ),
                "pullbackStop": (
                    levels.get("pullbackStop")
                    if levels
                    else None
                ),
                "pullbackRR": (
                    levels.get("pullbackRR")
                    if levels
                    else None
                ),
                "tp1": levels.get("tp1") if levels else None,
                "tp2": levels.get("tp2") if levels else None,
                "tp3": levels.get("tp3") if levels else None,
                "support15m": (
                    levels.get("support15m")
                    if levels
                    else None
                ),
                "resistance15m": (
                    levels.get("resistance15m")
                    if levels
                    else None
                ),
                "support1h": (
                    levels.get("support1h")
                    if levels
                    else None
                ),
                "resistance1h": (
                    levels.get("resistance1h")
                    if levels
                    else None
                ),
            },

            "ruleEngine": {
                "finalStatus": rules.get("finalStatus"),
                "confirmations": rules.get(
                    "confirmations"
                ),
                "reasonsToWatch": rules.get(
                    "reasonsToWatch"
                ),
                "reasonsToSkip": rules.get(
                    "reasonsToSkip"
                ),
            },
        })

    compact_json = json.dumps(
        compact_data,
        ensure_ascii=False,
        indent=2,
    )

    gpt_report = analyze_with_gpt(compact_json)

    return build_brief_report(
        rows,
        snapshot.get("runTimeUtc"),
        gpt_report,
    )

def main():
    if len(sys.argv) < 2:
        print("Usage: python analyzer.py data\\binance_snapshot_YYYYMMDD_HHMMSS.json")
        return

    input_path = Path(sys.argv[1])

    if not input_path.exists():
        print(f"File not found: {input_path}")
        return

    snapshot = load_snapshot(input_path)
    report = make_report(snapshot)

    output_path = input_path.with_name(input_path.stem.replace("binance_snapshot", "report") + ".txt")

    with open(output_path, "w", encoding="utf-8") as file:
        file.write(report)

    print(f"Report saved: {output_path}")


if __name__ == "__main__":
    main()