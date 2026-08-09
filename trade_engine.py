"""Compatibility facade for the refactored scanner pipeline.

New code should import from :mod:`scanner.pipeline` or application.scanner_service.
Legacy imports remain stable for Telegram, monitor jobs and tests.
"""
from scanner.pipeline import *  # noqa: F401,F403
from scanner.pipeline import (
    _strategy_profile,
    _profile_thresholds,
    _env_float,
    _best_rr,
    _direction_for_candles,
    _listing_metadata,
    _select_market_symbols,
    _set_scan_state,
)
