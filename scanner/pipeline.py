"""Scanner orchestration facade.

The pipeline coordinates four bounded stages: universe -> deep analysis -> signal
gate -> AI/finalization. Heavy implementation details live in focused modules.
"""
from __future__ import annotations
import gc
import threading
import hashlib
from datetime import datetime, timezone

from core.events import emit
from core.runtime_config import integer, number, string, scanner_config
from core.runtime_state import finish as runtime_finish, get as runtime_get, start as runtime_start, update as runtime_update
from trade_market_client import get_last_universe_summary
from scanner.universe import (collect_market_snapshot, _select_market_symbols, collect_and_analyze_market, get_priority_listing_symbols, get_priority_discovery_symbols)
from scanner.analysis import (_listing_metadata, analyze_snapshot, _direction_for_candles, build_timeframe_profile, build_trade_profile)
from scanner.signals import (_best_rr, row_to_signal, _env_float, _strategy_profile, _profile_thresholds, find_trade_signals)

_SCAN_LOCK = threading.Lock()

def _set_scan_state(**updates):
    runtime_update("scanner", **updates)

def get_trade_scan_runtime_state():
    return runtime_get("scanner")

def is_trade_scan_running():
    return _SCAN_LOCK.locked()

def run_trade_scan(include_watch=False, max_results=5, apply_ai=True, source='unknown', only_symbols=None):
    if not _SCAN_LOCK.acquire(blocking=False):
        return {
            'runTimeUtc': datetime.now(timezone.utc).isoformat(),
            'marketProvider': string('TRADE_MARKET_PROVIDER', 'unknown', strategy=False),
            'signals': [], 'rowsAnalyzed': 0, 'busy': True,
            'prioritySymbols': [], 'listingPrioritySymbols': [], 'discoveryPrioritySymbols': [],
            'scanState': get_trade_scan_runtime_state(),
        }
    runtime_start('scanner', owner=source, phase='universe', processed=0, total=0)
    snapshot = None
    rows = None
    try:
        listing_priority = get_priority_listing_symbols(limit=integer('TRADE_LISTING_PRIORITY_LIMIT', 12, minimum=0, maximum=100))
        discovery_priority = get_priority_discovery_symbols(limit=integer('TRADE_DISCOVERY_PRIORITY_LIMIT', 10, minimum=0, maximum=100))
        priority = list(dict.fromkeys(listing_priority + discovery_priority))
        top_limit = scanner_config().top_symbols
        snapshot, rows = collect_and_analyze_market(extra_symbols=None if only_symbols else priority, top_limit=top_limit, only_symbols=only_symbols)
        run_time = snapshot['runTimeUtc']
        provider = snapshot.get('marketProvider', 'unknown')
        universe_summary = get_last_universe_summary()
        universe_summary.setdefault('selectedSymbols', len(snapshot.get('selectedSymbols') or []))
        provider_stats = dict(snapshot.get('universeProviderStats') or {})
        _set_scan_state(phase='ranking', processed=len(rows), total=len(snapshot.get('selectedSymbols') or []))
        snapshot.clear()
        snapshot = None
        gc.collect()
        min_score = number('TRADE_MIN_SCORE', 72.0)
        min_rr = number('TRADE_MIN_RR', 2.0)
        min_probability = number('TRADE_MIN_PROBABILITY', 65.0)
        candidate_pool = max(max_results * 2, scanner_config().hedge_pool) if apply_ai else max_results
        filter_diag = {}
        signals = find_trade_signals(
            rows, min_score=min_score, min_rr=min_rr,
            include_watch=include_watch, max_results=candidate_pool, min_probability=min_probability,
            diagnostics=filter_diag,
        )
        ai_diag = {"input": len(signals), "quality": len(signals), "ev": len(signals), "passed": len(signals), "rejected": []}
        if apply_ai:
            _set_scan_state(phase='hedge', processed=0, total=len(signals))
            try:
                from ai_intelligence import rank_signals, get_last_rank_diagnostics
                signals = rank_signals(signals)[:max_results]
                ai_diag = get_last_rank_diagnostics()
            except Exception as exc:
                # Final AI/Hedge ranking is a safety gate. If it is unavailable,
                # never leak pre-ranked candidates to Telegram/Paper.
                ai_diag = {"input": len(signals), "quality": 0, "ev": 0, "passed": 0,
                           "rejected": [], "error": f"{type(exc).__name__}: {exc}"}
                emit('SIGNAL_RANKING_FAILED', source=source, error=ai_diag["error"])
                signals = []
        _set_scan_state(phase='finalizing', processed=len(signals), total=max(len(signals), 1))
        stages = {
            "analyzed": int(filter_diag.get("analyzed") or len(rows)),
            "status": int(filter_diag.get("status") or 0),
            "score": int(filter_diag.get("score") or 0),
            "rr": int(filter_diag.get("rr") or 0),
            "probability": int(filter_diag.get("probability") or 0),
            "quality": int(ai_diag.get("quality") or 0),
            "ev": int(ai_diag.get("ev") or 0),
            "signals": len(signals),
        }
        market_state = {"LONG_BIAS": 0, "SHORT_BIAS": 0, "NO_TRADE": 0}
        for _row in rows:
            direction = ((_row.get("score") or {}).get("direction") if isinstance(_row, dict) else None) or "NO_TRADE"
            market_state[direction if direction in market_state else "NO_TRADE"] += 1
        distributions = {
            "quality": dict(ai_diag.get("qualityBands") or {}),
            "probability": dict(ai_diag.get("probabilityBands") or {}),
            "ev": dict(ai_diag.get("evBands") or {}),
        }
        # One immutable event id per scanner run/signal. Structural fingerprints are
        # for similarity; event fingerprints are for identity/idempotency.
        def _attach_event_identity(item, idx, kind):
            if not isinstance(item, dict):
                return item
            if not item.get("signal_created_at"):
                item["signal_created_at"] = run_time
            if not item.get("event_id"):
                raw = f"{source}|{run_time}|{kind}|{idx}|{item.get('fingerprint') or item.get('symbol') or ''}"
                item["event_id"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            item.setdefault("eventFingerprint", item["event_id"])
            return item
        for _i,_signal in enumerate(signals):
            _attach_event_identity(_signal,_i,"signal")
        near_misses = list(ai_diag.get("rejected") or []) + list(filter_diag.get("nearMisses") or [])
        for _i,_near in enumerate(near_misses):
            _attach_event_identity(_near,_i,"near")
        # v22: near-signal candidates stay hot between full scans, while shadow
        # candidates are tracked without being sent/opened as trades.
        near_watch = []
        try:
            from near_signal_watchlist import select_near_candidates, upsert_near_candidates
            near_watch = select_near_candidates(near_misses)
            upsert_near_candidates(near_watch, source=source)
        except Exception:
            near_watch = []
        try:
            from shadow_signals import register_shadow_candidates
            register_shadow_candidates(near_misses, source=source)
        except Exception:
            pass
        for _signal in signals:
            emit('SIGNAL_CREATED', source=source, symbol=_signal.get('symbol'), direction=_signal.get('direction'), setup=_signal.get('setup'), fingerprint=_signal.get('fingerprint'))
        result = {
            'runTimeUtc': run_time, 'marketProvider': provider, 'signals': signals,
            'rowsAnalyzed': len(rows), 'prioritySymbols': priority,
            'listingPrioritySymbols': listing_priority, 'discoveryPrioritySymbols': discovery_priority,
            'universeSummary': universe_summary, 'universeProviderStats': provider_stats,
            'scannerStages': stages, 'nearMisses': near_misses[:8],
            'nearWatch': near_watch[:12], 'nearWatchSymbols': [x.get('symbol') for x in near_watch if x.get('symbol')],
            'scannerDistributions': distributions, 'marketState': market_state,
            'scanSource': source, 'targetedScan': bool(only_symbols),
        }
        try:
            from scanner_intelligence import save_scan_intelligence, build_recommendation
            intelligence = {
                "runTimeUtc": run_time, "marketProvider": provider,
                "universe": universe_summary, "providerStats": provider_stats,
                "stages": stages, "nearMisses": near_misses[:8],
                "distributions": distributions, "marketState": market_state,
                "recommendation": "",
            }
            intelligence["recommendation"] = build_recommendation(intelligence)
            save_scan_intelligence(intelligence)
        except Exception:
            pass
        return result
    finally:
        if snapshot is not None:
            snapshot.clear()
        if rows is not None:
            rows.clear()
        try:
            from memory_guard import cleanup
            cleanup()
        except Exception:
            gc.collect()
        emit('SCAN_FINISHED', source=source)
        runtime_finish('scanner')
        _SCAN_LOCK.release()
