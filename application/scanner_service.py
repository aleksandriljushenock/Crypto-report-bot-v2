from __future__ import annotations
from core.runtime_state import get as runtime_get

def runtime_status():
    return runtime_get("scanner")

def run(**kwargs):
    from trade_engine import run_trade_scan
    return run_trade_scan(**kwargs)
