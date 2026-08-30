"""Central dynamic runtime configuration facade.

Values are read lazily so tests, Railway env changes after process restart, and
Supabase-backed strategy settings can keep the existing semantics. Infrastructure
secrets remain environment-owned; strategy values prefer strategy_settings when
that module knows the key.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

_TRUE = {"1", "true", "yes", "on", "да"}


def _strategy_value(name: str) -> Any:
    try:
        from strategy_settings import SPEC_BY_KEY, current_value
        if name in SPEC_BY_KEY:
            return current_value(name)
    except Exception:
        pass
    return None


def raw(name: str, default: Any = None, *, strategy: bool = True) -> Any:
    if strategy:
        value = _strategy_value(name)
        if value is not None:
            return value
    value = os.getenv(name)
    return default if value is None else value


def string(name: str, default: str = "", *, strategy: bool = True) -> str:
    value = raw(name, default, strategy=strategy)
    return str(default if value is None else value).strip()


def boolean(name: str, default: bool = False, *, strategy: bool = True) -> bool:
    value = raw(name, default, strategy=strategy)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE


def integer(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None, strategy: bool = True) -> int:
    try:
        value = int(float(raw(name, default, strategy=strategy)))
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def number(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None, strategy: bool = True) -> float:
    try:
        value = float(raw(name, default, strategy=strategy))
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def csv(name: str, default: Iterable[str] = (), *, strategy: bool = True) -> list[str]:
    fallback = ",".join(default)
    return [part.strip().lower() for part in string(name, fallback, strategy=strategy).split(",") if part.strip()]


DEFAULT_PROVIDERS = ("binance", "bybit", "okx", "bitget", "gate", "mexc", "bingx", "kucoin", "hyperliquid", "htx")


@dataclass(frozen=True)
class ScannerConfig:
    multi_exchange: bool
    top_symbols: int
    fast_pool: int
    batch_size: int
    workers: int
    hedge_pool: int
    min_quote_volume: float
    universe_timeout: int
    min_venues: int
    coverage_bonus: float


def scanner_config() -> ScannerConfig:
    from config import MIN_QUOTE_VOLUME_USDT
    low_memory = boolean("LOW_MEMORY_MODE", False, strategy=False)
    # Infrastructure safety caps are environment-owned and cannot be weakened by
    # Supabase strategy settings.  This keeps a 512 MB deployment memory-safe.
    workers = integer("TRADE_SCAN_MAX_WORKERS", 4, minimum=1, maximum=8)
    top_symbols = integer("TRADE_TOP_LIQUID_SYMBOLS", 150, minimum=1, maximum=500)
    batch_size = integer("TRADE_SCAN_BATCH_SIZE", 16, minimum=2, maximum=32)
    hedge_pool = integer("HEDGE_CANDIDATE_POOL", 40, minimum=1, maximum=250)
    if low_memory:
        workers = min(workers, integer("LOW_MEMORY_MAX_SCAN_WORKERS", 2, minimum=1, maximum=4, strategy=False))
        top_symbols = min(top_symbols, integer("LOW_MEMORY_MAX_TOP_SYMBOLS", 90, minimum=20, maximum=200, strategy=False))
        batch_size = min(batch_size, integer("LOW_MEMORY_MAX_SCAN_BATCH", 8, minimum=2, maximum=16, strategy=False))
        hedge_pool = min(hedge_pool, integer("LOW_MEMORY_MAX_HEDGE_POOL", 20, minimum=5, maximum=60, strategy=False))
    return ScannerConfig(
        multi_exchange=boolean("MULTI_EXCHANGE_UNIVERSE_ENABLED", True),
        top_symbols=top_symbols,
        fast_pool=integer("FAST_SCAN_POOL_SIZE", 500, minimum=1, maximum=2000),
        batch_size=batch_size,
        workers=workers,
        hedge_pool=hedge_pool,
        min_quote_volume=number("MULTI_EXCHANGE_MIN_QUOTE_VOLUME_USDT", float(MIN_QUOTE_VOLUME_USDT), minimum=0),
        universe_timeout=integer("MULTI_EXCHANGE_UNIVERSE_TIMEOUT", 8, minimum=1, maximum=60),
        min_venues=integer("MULTI_EXCHANGE_MIN_VENUES", 1, minimum=1, maximum=len(DEFAULT_PROVIDERS)),
        coverage_bonus=number("MULTI_EXCHANGE_COVERAGE_BONUS", 0.08, minimum=0, maximum=2),
    )

@dataclass(frozen=True)
class PaperConfig:
    enabled: bool
    initial_balance: float
    max_positions: int
    one_per_symbol: bool
    max_leverage: int
    liquidation_buffer_pct: float
    maintenance_margin_pct: float
    fee_pct_per_side: float
    exit_slippage_pct: float
    entry_slippage_pct: float
    max_hold_hours: int
    entry_wait_hours: int
    max_entry_deviation_pct: float
    min_free_balance: float


def paper_config() -> PaperConfig:
    return PaperConfig(
        enabled=boolean("PAPER_TRADING_ENABLED", True),
        initial_balance=number("PAPER_INITIAL_BALANCE_USD", 100.0, minimum=1),
        max_positions=integer("PAPER_MAX_OPEN_POSITIONS", 10, minimum=1, maximum=100),
        one_per_symbol=boolean("PAPER_ONE_POSITION_PER_SYMBOL", True),
        max_leverage=integer("PAPER_MAX_LEVERAGE", 20, minimum=1, maximum=125),
        liquidation_buffer_pct=number("PAPER_LIQUIDATION_BUFFER_PCT", 0.5, minimum=0),
        maintenance_margin_pct=number("PAPER_MAINTENANCE_MARGIN_PCT", 0.5, minimum=0),
        fee_pct_per_side=number("PAPER_FEE_PCT_PER_SIDE", 0.06, minimum=0),
        exit_slippage_pct=number("PAPER_SLIPPAGE_PCT", 0.03, minimum=0),
        entry_slippage_pct=number("PAPER_ENTRY_SLIPPAGE_PCT", 0.03, minimum=0),
        max_hold_hours=integer("PAPER_MAX_HOLD_HOURS", 72, minimum=1, maximum=720),
        entry_wait_hours=integer("PAPER_ENTRY_MAX_WAIT_HOURS", 12, minimum=1, maximum=168),
        max_entry_deviation_pct=number("PAPER_MAX_ENTRY_DEVIATION_PCT", 0.50, minimum=0, maximum=20),
        min_free_balance=number("PAPER_MIN_FREE_BALANCE_USD", 5.0, minimum=0),
    )


@dataclass(frozen=True)
class NearSignalConfig:
    enabled: bool
    rescan_minutes: int
    rescan_limit: int
    min_distance_pct: float
    ttl_hours: int


def near_signal_config() -> NearSignalConfig:
    return NearSignalConfig(
        enabled=boolean("NEAR_SIGNAL_WATCH_ENABLED", True),
        rescan_minutes=integer("NEAR_SIGNAL_RESCAN_MINUTES", 5, minimum=1, maximum=60),
        rescan_limit=integer("NEAR_SIGNAL_RESCAN_LIMIT", 40, minimum=1, maximum=200),
        min_distance_pct=number("NEAR_SIGNAL_MIN_DISTANCE_PCT", 85.0, minimum=50, maximum=100),
        ttl_hours=integer("NEAR_SIGNAL_TTL_HOURS", 12, minimum=1, maximum=168),
    )


@dataclass(frozen=True)
class OptimizerConfig:
    enabled: bool
    interval_minutes: int
    min_trades: int
    min_retention: float
    adaptive_enabled: bool
    adaptive_min_trades: int
    adaptive_min_validation: int
    adaptive_blend_weight: float


def optimizer_config() -> OptimizerConfig:
    return OptimizerConfig(
        enabled=boolean("AI_OPTIMIZER_ENABLED", True),
        interval_minutes=integer("AI_OPTIMIZER_INTERVAL_MINUTES", 1440, minimum=60),
        min_trades=integer("AI_OPTIMIZER_MIN_TRADES", 20, minimum=10),
        min_retention=number("AI_OPTIMIZER_MIN_RETENTION", 0.70, minimum=0.30, maximum=1.0),
        adaptive_enabled=boolean("ADAPTIVE_MODEL_ENABLED", True),
        adaptive_min_trades=integer("ADAPTIVE_MODEL_MIN_TRADES", 40, minimum=20),
        adaptive_min_validation=integer("ADAPTIVE_MODEL_MIN_VALIDATION", 12, minimum=8),
        adaptive_blend_weight=number("ADAPTIVE_MODEL_BLEND_WEIGHT", 0.20, minimum=0, maximum=0.45),
    )
