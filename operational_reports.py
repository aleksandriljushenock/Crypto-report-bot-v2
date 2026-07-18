"""Telegram diagnostics for market, regime, confidence, features and health."""
from __future__ import annotations
import os, platform, sqlite3, sys
from pathlib import Path
from learning_max2 import status
from trade_signal_store import get_recent_signals

def _latest_payload():
    rows = get_recent_signals(limit=1)
    return (rows[0].get("payload") or {}) if rows else {}

def build_market_report():
    p=_latest_payload(); return "\n".join(["<b>🌍 MARKET</b>", f"Symbol: <b>{p.get('symbol','n/a')}</b>", f"Direction: <b>{p.get('direction','n/a')}</b>", f"Score: <b>{p.get('aiScore',p.get('score','n/a'))}</b>"])

def build_regime_report():
    p=_latest_payload(); factors=p.get('aiFactors') or {}; from learning_engine_v14 import classify_regime
    return f"<b>🧭 MARKET REGIME</b>\nТекущий режим: <b>{p.get('marketRegime') or (classify_regime(factors) if factors else 'нет данных')}</b>"

def build_confidence_report():
    p=_latest_payload(); return f"<b>🎯 CONFIDENCE</b>\nВероятность: <b>{p.get('probability','n/a')}</b>\nУверенность: <b>{p.get('confidence','n/a')}</b>\nНеопределённость: <b>{p.get('uncertainty','n/a')}</b>"

def build_features_report():
    p=_latest_payload(); factors=p.get('aiFactors') or {}; rows=sorted(factors.items(), key=lambda x: float(x[1] or 0), reverse=True)
    return "\n".join(["<b>🧩 FEATURES</b>", ""]+[f"• {k}: <b>{float(v):.1f}</b>" for k,v in rows[:15]]) if rows else "<b>🧩 FEATURES</b>\nНет сохранённых признаков."

def build_health_report():
    data=status(); db_files=list(Path('data').glob('*.db')); db_size=sum(p.stat().st_size for p in db_files if p.exists())
    return "\n".join(["<b>🩺 HEALTH</b>", f"Python: <code>{sys.version.split()[0]}</code>", f"Platform: <code>{platform.system()}</code>", f"DB files: <b>{len(db_files)}</b> ({db_size/1024/1024:.1f} MB)", f"Feature store: <b>{data.get('stored_signals',0)}</b>", f"Completed: <b>{data.get('completed',0)}</b>", "Status: <b>OK</b>"])
