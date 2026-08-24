from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

SNAPSHOT = Path(os.getenv('ADAPTIVE_CLOUD_SNAPSHOT', 'data/adaptive_cloud_weights.json'))


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _target(row: dict) -> float | None:
    result = _as_dict(row.get('real_result') or row.get('result') or row.get('outcome'))
    for key in ('return_percent', 'returnPercent', 'pnl_percent', 'pnl', 'target', 'success'):
        if key in result:
            value = result[key]
            if isinstance(value, bool):
                return 1.0 if value else -1.0
            try:
                value = float(value)
                return max(-20.0, min(20.0, value))
            except Exception:
                pass
    return None


def train_cloud_overlay(base_weights: dict[str, float], min_samples: int | None = None) -> dict:
    min_samples = min_samples or int(os.getenv('ADAPTIVE_CLOUD_MIN_SAMPLES', '20'))
    try:
        from cloud_learning_store import CloudLearningStore
        rows = CloudLearningStore().resolved_rows(limit=int(os.getenv('ADAPTIVE_CLOUD_MAX_ROWS', '1000')))
    except Exception as exc:
        return {'status': 'cloud-unavailable', 'error': str(exc), 'samples': 0}

    parsed = []
    for row in rows:
        features = _as_dict(row.get('features'))
        factors = _as_dict(features.get('aiFactors') or features.get('tradeProfile')) or features
        target = _target(row)
        if factors and target is not None:
            parsed.append((factors, target))
    if len(parsed) < min_samples:
        return {'status': 'collecting-data', 'samples': len(parsed), 'required': min_samples}

    mean_y = sum(y for _, y in parsed) / len(parsed)
    max_step = float(os.getenv('ADAPTIVE_WEIGHT_MAX_STEP', '0.12'))
    learning_rate = float(os.getenv('ADAPTIVE_WEIGHT_LEARNING_RATE', '0.35'))
    adjusted = {}
    diagnostics = {}
    for key, base in base_weights.items():
        values = [(float(f.get(key, 50) or 50), y) for f, y in parsed if key in f]
        if len(values) < max(8, min_samples // 2):
            adjusted[key] = float(base)
            continue
        mean_x = sum(x for x, _ in values) / len(values)
        cov = sum((x - mean_x) * (y - mean_y) for x, y in values) / len(values)
        var = sum((x - mean_x) ** 2 for x, _ in values) / len(values)
        signal = cov / (math.sqrt(var) * 10 + 1e-9)
        delta = max(-max_step, min(max_step, signal * learning_rate))
        adjusted[key] = round(max(0.2, float(base) * (1.0 + delta)), 4)
        diagnostics[key] = round(delta, 4)

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    payload = {'status': 'updated', 'samples': len(parsed), 'weights': adjusted, 'deltas': diagnostics}
    tmp = SNAPSHOT.with_name(SNAPSHOT.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, SNAPSHOT)
    return payload


def load_cloud_overlay(base_weights: dict[str, float]) -> dict[str, float]:
    try:
        data = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
        weights = data.get('weights') or {}
        return {k: float(weights.get(k, v)) for k, v in base_weights.items()}
    except Exception:
        return dict(base_weights)
