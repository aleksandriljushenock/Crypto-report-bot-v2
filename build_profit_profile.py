"""Rebuild data/profit_profile_v2.json without pandas or hard-coded /mnt/data paths.

Default source is durable Supabase learning observations. A CSV export can be
supplied with --input. The output path defaults to PROFIT_PROFILE_PATH.
V41 writes several recent windows so Telegram recency settings work at runtime.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

GROUPS = ('setup','regime','structure1h','structure15m','tf1d','tf4h','tf1h','tf15m','tf5m','symbol')
FACTORS = ('trend','momentum','volume','funding','open_interest','alignment','risk_reward','capital_flow','smart_money','news','narrative')
DEFAULT_WINDOWS = (7, 14, 21, 30, 60, 90, 180)


def _json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _normalize(source: Dict[str, Any]) -> Dict[str, Any] | None:
    f = _json(source.get('features') or source.get('payload_json') or source.get('signal_payload'))
    result = _json(source.get('real_result') or source.get('result') or source.get('outcome'))
    factors = f.get('aiFactors') or f.get('tradeProfile') or {}
    ret = _num(result.get('return_percent', result.get('returnPercent', result.get('pnl_percent', source.get('price_change_pct')))), 0.0)
    success_raw = result.get('success')
    if success_raw is None:
        success_raw = ret > 0
    created = source.get('signal_created_at') or source.get('created_at') or f.get('createdAt') or f.get('timestamp')
    item = {
        'created_at': created,
        '_dt': _dt(created),
        'symbol': source.get('symbol') or f.get('symbol') or 'UNKNOWN',
        'direction': source.get('signal_direction') or f.get('direction') or '',
        'return': ret,
        'win': 1 if bool(success_raw) else 0,
        'setup': f.get('setup', 'NONE'),
        'regime': f.get('marketRegime') or f.get('aiRegime') or 'unknown',
        'structure1h': f.get('structure1h', 'N/A'),
        'structure15m': f.get('structure15m', 'N/A'),
        'score': _num(source.get('signal_score') or f.get('aiScore') or f.get('score')),
        'probability': _num(f.get('probability') or source.get('signal_confidence')),
        'confidence': _num(f.get('confidence')),
        'uncertainty': _num(f.get('uncertainty'), 100),
        'quoteVolume': _num(f.get('quoteVolume')),
        'rr': _num(f.get('rr')),
    }
    tfs = f.get('timeframes') or {}
    for tf in ('1d','4h','1h','15m','5m'):
        item['tf' + tf] = tfs.get(tf, 'N/A')
    for key in FACTORS:
        item[key] = _num(factors.get(key), 50.0)
    return item


def _stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {'samples': 0, 'win_rate': 0.0, 'avg_return': 0.0, 'avg_win': 0.0, 'avg_loss': 0.0, 'profit_factor': 0.0}
    wins = sum(int(r['win']) for r in rows)
    returns = [_num(r['return']) for r in rows]
    positives = [x for x in returns if x > 0]
    negatives = [x for x in returns if x < 0]
    gross_profit = sum(positives)
    gross_loss = abs(sum(negatives))
    return {
        'samples': n,
        'win_rate': round(wins / n * 100.0, 2),
        'avg_return': round(sum(returns) / n, 4),
        'avg_win': round(sum(positives) / len(positives), 4) if positives else 0.0,
        'avg_loss': round(abs(sum(negatives) / len(negatives)), 4) if negatives else 0.0,
        'profit_factor': round(gross_profit / gross_loss, 4) if gross_loss else (99.0 if gross_profit else 0.0),
    }


def _groups(rows: List[Dict[str, Any]], min_samples: int = 8) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for col in GROUPS:
        bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            bucket[str(row.get(col, 'N/A'))].append(row)
        out[col] = {key: _stats(items) for key, items in bucket.items() if len(items) >= min_samples}
    return out


def _rule(rows: List[Dict[str, Any]], kind: str, name: str, pred: Callable[[Dict[str, Any]], bool], adjustment: float) -> Dict[str, Any] | None:
    subset = [r for r in rows if pred(r)]
    if len(subset) < 8:
        return None
    item = _stats(subset)
    item.update({'kind': kind, 'name': name, 'adjustment': adjustment})
    return item


def _rules(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    specs = [
        ('boost','PULLBACK',lambda r:str(r['setup']).upper()=='PULLBACK',5),
        ('penalty','BREAKOUT',lambda r:str(r['setup']).upper()=='BREAKOUT',-5),
        ('boost','flow_alignment_volume',lambda r:r['capital_flow']>=62 and r['alignment']>=75 and r['volume']>=65,9),
        ('boost','smart_pullback_volume',lambda r:r['smart_money']>=60 and str(r['setup']).upper()=='PULLBACK' and r['volume']>=65,7),
        ('boost','smart_pullback_probability',lambda r:r['smart_money']>=60 and str(r['setup']).upper()=='PULLBACK' and r['probability']>=70,6),
        ('boost','probability_trend_liquidity',lambda r:r['probability']>=72 and r['trend']>=85 and r['quoteVolume']>=130_000_000,6),
        ('boost','daily_micro_alignment',lambda r:r['tf1d']=='UP' and r['tf5m']=='UP' and r['alignment']>=75,5),
        ('boost','structure_1h_long',lambda r:r['structure1h'] in ('SWEEP_HIGH','BOS_UP'),4),
        ('penalty','low_liquidity',lambda r:r['quoteVolume']<130_000_000,-8),
        ('penalty','weak_capital_flow',lambda r:r['capital_flow']<=50,-8),
        ('penalty','low_confidence',lambda r:r['confidence']<36,-7),
        ('penalty','high_uncertainty',lambda r:r['uncertainty']>64,-7),
        ('penalty','long_against_4h',lambda r:r['tf4h']=='DOWN',-12),
        ('penalty','weak_breakout',lambda r:str(r['setup']).upper()=='BREAKOUT' and r['volume']<60,-8),
        ('penalty','breakout_micro_weak',lambda r:str(r['setup']).upper()=='BREAKOUT' and r['tf5m'] in ('DOWN','RANGE'),-6),
    ]
    return [item for item in (_rule(rows, *spec) for spec in specs) if item]


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open('r', encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh))


def _load_supabase(limit: int) -> List[Dict[str, Any]]:
    from cloud_learning_store import CloudLearningStore
    return list(CloudLearningStore().resolved_rows(limit=limit))


def build(rows_raw: Iterable[Dict[str, Any]], windows: Iterable[int] = DEFAULT_WINDOWS) -> Dict[str, Any]:
    rows = [r for raw in rows_raw if (r := _normalize(raw)) is not None]
    if not rows:
        raise RuntimeError('no usable resolved observations found')
    now = datetime.now(timezone.utc)
    windows = sorted({max(1, int(x)) for x in windows})
    recent_windows = {}
    for days in windows:
        cutoff = now - timedelta(days=days)
        recent = [r for r in rows if r.get('_dt') and r['_dt'] >= cutoff]
        recent_windows[str(days)] = _groups(recent)
    profile = {
        'version': 'profit-profile-v41-' + now.strftime('%Y%m%d%H%M%S'),
        'generated_at': now.isoformat(),
        'overall': _stats(rows),
        'groups': _groups(rows),
        'recent_windows': recent_windows,
        'recent_window_options': windows,
        'rules': _rules(rows),
    }
    return profile



def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def rebuild_from_supabase(output: str | Path | None = None, limit: int | None = None, windows: Iterable[int] = DEFAULT_WINDOWS) -> Dict[str, Any]:
    """Rebuild the runtime profile from durable Supabase observations."""
    max_rows = int(limit or os.getenv('PROFILE_REBUILD_MAX_ROWS', '10000'))
    profile = build(_load_supabase(max(100, max_rows)), windows)
    out = Path(output or os.getenv('PROFIT_PROFILE_PATH', 'data/profit_profile_v2.json'))
    _atomic_write_json(out, profile)
    try:
        import ai_hedge_fund_engine
        ai_hedge_fund_engine._CACHE = None
    except Exception:
        pass
    return {'status': 'ok', 'path': str(out), 'samples': profile['overall']['samples'], 'windows': profile['recent_window_options'], 'version': profile['version']}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', help='Optional CSV export. If omitted, Supabase resolved observations are used.')
    parser.add_argument('--output', default=os.getenv('PROFIT_PROFILE_PATH', 'data/profit_profile_v2.json'))
    parser.add_argument('--limit', type=int, default=int(os.getenv('PROFILE_REBUILD_MAX_ROWS', '10000')))
    parser.add_argument('--windows', default=os.getenv('PROFILE_RECENT_WINDOWS_DAYS', '7,14,21,30,60,90,180'))
    args = parser.parse_args()
    rows_raw = _load_csv(Path(args.input)) if args.input else _load_supabase(max(100, args.limit))
    windows = [int(x.strip()) for x in args.windows.split(',') if x.strip()]
    profile = build(rows_raw, windows)
    out = Path(args.output)
    _atomic_write_json(out, profile)
    print(f"{out} samples={profile['overall']['samples']} windows={profile['recent_window_options']}")


if __name__ == '__main__':
    main()
