from __future__ import annotations

import gc
import json
import logging
import math
import os
import subprocess
import sys
import threading
import urllib.request
from core.runtime_config import boolean, integer, number, string
from typing import Any, Sequence

logger = logging.getLogger(__name__)
_MODEL = None
_MODEL_LOCK = threading.Lock()
_LOAD_FAILED = False
_FORECAST_COUNT = 0


def _bool(name: str, default: bool) -> bool:
    return boolean(name, default)


def _enabled() -> bool:
    # strategy_settings is loaded from Supabase at runtime startup and mirrored to
    # os.environ. current_value also preserves the ENV fallback for local runs.
    try:
        from strategy_settings import current_value
        return str(current_value("CHRONOS_ENABLED")).lower() == "true"
    except Exception:
        return boolean("CHRONOS_ENABLED", False)

def chronos_enabled() -> bool:
    return _enabled()

def set_chronos_enabled(enabled: bool) -> bool:
    state = bool(enabled)
    try:
        from strategy_settings import save_setting
        save_setting("CHRONOS_ENABLED", state, updated_by="telegram")
    except Exception:
        # Supabase may be temporarily unavailable. Keep a process-local fallback
        # so the user can still disable a memory-heavy model immediately.
        import os
        os.environ["CHRONOS_ENABLED"] = "true" if state else "false"
    if not state:
        unload_pipeline("telegram-disabled")
    return state


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _memory_allows_load() -> bool:
    if not _bool('CHRONOS_MEMORY_GUARD_ENABLED', True):
        return True
    try:
        from memory_guard import cleanup, rss_mb
        cleanup()
        current = rss_mb()
        hard = number('MEMORY_HARD_LIMIT_MB', 500.0, strategy=False)
        headroom = number('CHRONOS_REQUIRED_HEADROOM_MB', 230.0, strategy=False)
        allowed = current + headroom < hard
        if not allowed:
            logger.warning('Chronos skipped by memory guard: rss=%.1fMB required_headroom=%.1fMB hard=%.1fMB', current, headroom, hard)
        return allowed
    except Exception:
        return True


def unload_pipeline(reason: str = 'batch-complete') -> dict[str, Any]:
    global _MODEL
    had_model = _MODEL is not None
    _MODEL = None
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except Exception:
        pass
    try:
        from memory_guard import rss_mb
        rss = rss_mb()
    except Exception:
        rss = 0.0
    if had_model:
        logger.info('Chronos unloaded: reason=%s rss=%.1fMB', reason, rss)
    return {'unloaded': had_model, 'reason': reason, 'rssMb': rss}


def _remote_forecast(clean: list[float], horizon: int) -> dict[str, Any] | None:
    endpoint = string('CHRONOS_REMOTE_URL', '', strategy=False)
    if not endpoint:
        return None
    payload = json.dumps({'context': clean, 'prediction_length': horizon}).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    token = string('CHRONOS_REMOTE_TOKEN', '', strategy=False)
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(endpoint, data=payload, headers=headers, method='POST')
    timeout = max(5, integer('CHRONOS_REMOTE_TIMEOUT_SECONDS', 45, minimum=5, strategy=False))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode('utf-8'))
    return data if isinstance(data, dict) else None



def _subprocess_forecast(clean: list[float], horizon: int) -> dict[str, Any] | None:
    timeout = max(30, integer('CHRONOS_SUBPROCESS_TIMEOUT_SECONDS', 150, minimum=30, strategy=False))
    worker = os.path.join(os.path.dirname(__file__), 'chronos_worker.py')
    payload = json.dumps({'context': clean, 'prediction_length': horizon})
    env = os.environ.copy()
    env.setdefault('OMP_NUM_THREADS', '1')
    env.setdefault('MKL_NUM_THREADS', '1')
    env.setdefault('OPENBLAS_NUM_THREADS', '1')
    completed = subprocess.run(
        [sys.executable, worker], input=payload, text=True,
        capture_output=True, timeout=timeout, env=env, check=False,
    )
    if completed.returncode != 0:
        logger.warning('Chronos subprocess failed: code=%s stderr=%s', completed.returncode, completed.stderr[-500:])
        return None
    try:
        data = json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception:
        logger.warning('Chronos subprocess returned invalid JSON: %s', completed.stdout[-500:])
        return None
    return data if isinstance(data, dict) and not data.get('error') else None


def _load_pipeline():
    global _MODEL, _LOAD_FAILED
    if _MODEL is not None:
        return _MODEL
    if _LOAD_FAILED or not _enabled() or not _memory_allows_load():
        return None
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        if _LOAD_FAILED or not _memory_allows_load():
            return None
        try:
            import torch
            from chronos import BaseChronosPipeline
            model_name = string('CHRONOS_MODEL', 'amazon/chronos-bolt-tiny', strategy=False)
            dtype_name = string('CHRONOS_TORCH_DTYPE', 'float32', strategy=False).lower()
            dtype = {'float32': torch.float32, 'bfloat16': torch.bfloat16, 'float16': torch.float16}.get(dtype_name, torch.float32)
            _MODEL = BaseChronosPipeline.from_pretrained(model_name, device_map='cpu', torch_dtype=dtype)
            logger.info('Chronos loaded: model=%s dtype=%s', model_name, dtype_name)
            return _MODEL
        except Exception:
            _LOAD_FAILED = True
            logger.exception('Chronos could not be loaded; forecasts disabled for this process')
            unload_pipeline('load-failed')
            return None


def forecast_closes(closes: Sequence[float], prediction_length: int | None = None) -> dict[str, Any] | None:
    global _FORECAST_COUNT
    if not _enabled():
        return None
    min_context = max(32, integer('CHRONOS_MIN_CONTEXT', 64, minimum=32, strategy=False))
    max_context = max(min_context, integer('CHRONOS_CONTEXT_LENGTH', 128, minimum=32, strategy=False))
    clean = [_safe_float(x, float('nan')) for x in closes]
    clean = [x for x in clean if math.isfinite(x) and x > 0]
    if len(clean) < min_context:
        return None
    clean = clean[-max_context:]
    horizon = prediction_length or integer('CHRONOS_PREDICTION_LENGTH', 8, minimum=1, maximum=32, strategy=False)
    horizon = max(1, min(32, int(horizon)))

    mode = string('CHRONOS_MODE', 'subprocess', strategy=False).lower()
    if mode == 'remote':
        try:
            return _remote_forecast(clean, horizon)
        except Exception:
            logger.exception('Chronos remote inference failed')
            return None
    if mode == 'subprocess':
        try:
            return _subprocess_forecast(clean, horizon)
        except subprocess.TimeoutExpired:
            logger.warning('Chronos subprocess timeout after configured limit')
            return None
        except Exception:
            logger.exception('Chronos subprocess inference failed')
            return None

    pipeline = _load_pipeline()
    if pipeline is None:
        return None
    try:
        import torch
        with torch.inference_mode():
            context = torch.tensor(clean, dtype=torch.float32)
            quantiles, mean = pipeline.predict_quantiles(context, prediction_length=horizon, quantile_levels=[0.1, 0.5, 0.9])
        q = quantiles.detach().cpu()[0]
        avg = mean.detach().cpu()[0]
        last = clean[-1]
        q10, q50, q90 = float(q[-1, 0]), float(q[-1, 1]), float(q[-1, 2])
        mean_last = float(avg[-1])
        median_return = (q50 / last - 1.0) * 100.0
        mean_return = (mean_last / last - 1.0) * 100.0
        lower_return = (q10 / last - 1.0) * 100.0
        upper_return = (q90 / last - 1.0) * 100.0
        interval = max(1e-9, q90 - q10)
        sigma = max(interval / 2.563, last * 1e-6)
        probability_up = _normal_cdf((q50 - last) / sigma) * 100.0
        uncertainty_pct = interval / last * 100.0
        strength = min(100.0, abs(median_return) / max(uncertainty_pct, 0.05) * 50.0)
        _FORECAST_COUNT += 1
        return {
            'provider': 'amazon-chronos-bolt', 'model': string('CHRONOS_MODEL', 'amazon/chronos-bolt-tiny', strategy=False),
            'contextPoints': len(clean), 'predictionLength': horizon, 'lastPrice': round(last, 10),
            'forecastPrice': round(q50, 10), 'meanForecastPrice': round(mean_last, 10),
            'forecastReturnPct': round(median_return, 4), 'meanReturnPct': round(mean_return, 4),
            'lowerReturnPct': round(lower_return, 4), 'upperReturnPct': round(upper_return, 4),
            'probabilityUp': round(max(0.0, min(100.0, probability_up)), 2),
            'probabilityDown': round(max(0.0, min(100.0, 100.0 - probability_up)), 2),
            'uncertaintyPct': round(uncertainty_pct, 4), 'strength': round(strength, 2),
        }
    except Exception:
        logger.exception('Chronos inference failed')
        return None


def blend_signal(signal: dict[str, Any], forecast: dict[str, Any] | None) -> dict[str, Any]:
    if not forecast:
        signal['chronosStatus'] = 'skipped'
        return signal
    direction = str(signal.get('direction') or '').upper()
    if direction in {'LONG', 'LONG_BIAS', 'BUY'}:
        chronos_probability = _safe_float(forecast.get('probabilityUp'), 50.0)
        aligned_return = _safe_float(forecast.get('forecastReturnPct'))
    elif direction in {'SHORT', 'SHORT_BIAS', 'SELL'}:
        chronos_probability = _safe_float(forecast.get('probabilityDown'), 50.0)
        aligned_return = -_safe_float(forecast.get('forecastReturnPct'))
    else:
        chronos_probability, aligned_return = 50.0, 0.0
    uncertainty = _safe_float(forecast.get('uncertaintyPct'), 100.0)
    max_weight = max(0.0, min(0.35, number('CHRONOS_MAX_WEIGHT', 0.18, minimum=0.0, maximum=0.35)))
    reliability = max(0.10, min(1.0, 2.0 / max(uncertainty, 0.25)))
    weight = max_weight * reliability
    # V52 keeps Chronos in shadow mode by default until an operator explicitly
    # enables probability blending after out-of-sample uplift is demonstrated.
    blend_enabled = _bool('CHRONOS_PROBABILITY_BLEND_ENABLED', False)
    effective_weight = weight if blend_enabled else 0.0
    old_probability = _safe_float(signal.get('probability'), 50.0)
    blended = old_probability * (1.0 - effective_weight) + chronos_probability * effective_weight
    agreement = aligned_return > 0
    old_confidence = _safe_float(signal.get('confidence'), 50.0)
    confidence_delta = (5.0 * reliability) if agreement else (-7.0 * reliability)
    signal['probabilityBeforeChronos'] = round(old_probability, 2)
    signal['probability'] = round(max(5.0, min(95.0, blended)), 2)
    signal['confidence'] = round(max(5.0, min(95.0, old_confidence + confidence_delta)), 2)
    signal['chronosStatus'] = 'applied'
    signal['chronos'] = {**forecast, 'weight': round(effective_weight, 4), 'shadowWeight': round(weight, 4), 'shadowOnly': (not blend_enabled), 'directionAgreement': agreement}
    profile = signal.get('tradeProfile')
    if isinstance(profile, dict):
        profile['chronos'] = round(chronos_probability, 2)
        profile['chronosWeight'] = round(effective_weight, 4)
        profile['probability'] = signal['probability']
        profile['confidence'] = signal['confidence']
    return signal


def apply_to_finalists(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _enabled() or not signals:
        for signal in signals:
            signal.pop('_chronosCloses', None)
        return signals
    limit = max(1, min(5, integer('CHRONOS_FINALISTS', 3, minimum=1, maximum=5)))
    for index, signal in enumerate(signals):
        closes = signal.pop('_chronosCloses', None) or []
        if index >= limit:
            signal['chronosStatus'] = 'not-finalist'
            continue
        forecast = forecast_closes(closes)
        blend_signal(signal, forecast)
    if _bool('CHRONOS_UNLOAD_AFTER_BATCH', True):
        unload_pipeline('finalists-complete')
    return signals
