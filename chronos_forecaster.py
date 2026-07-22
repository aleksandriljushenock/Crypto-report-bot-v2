from __future__ import annotations

import logging
import math
import os
import threading
from typing import Any, Sequence

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_LOCK = threading.Lock()
_LOAD_FAILED = False


def _enabled() -> bool:
    return os.getenv("CHRONOS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _load_pipeline():
    global _MODEL, _LOAD_FAILED
    if _MODEL is not None:
        return _MODEL
    if _LOAD_FAILED or not _enabled():
        return None
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        if _LOAD_FAILED:
            return None
        try:
            import torch
            from chronos import BaseChronosPipeline

            model_name = os.getenv("CHRONOS_MODEL", "amazon/chronos-bolt-tiny").strip()
            _MODEL = BaseChronosPipeline.from_pretrained(
                model_name,
                device_map="cpu",
                torch_dtype=torch.float32,
            )
            logger.info("Chronos loaded: model=%s", model_name)
            return _MODEL
        except Exception:
            _LOAD_FAILED = True
            logger.exception("Chronos could not be loaded; forecasts disabled for this process")
            return None


def forecast_closes(closes: Sequence[float], prediction_length: int | None = None) -> dict[str, Any] | None:
    """Return a lightweight zero-shot price forecast from Chronos-Bolt.

    The method is fail-open: any model/download/inference error returns None and
    the existing rule/learning pipeline continues unchanged.
    """
    if not _enabled():
        return None

    min_context = max(32, int(os.getenv("CHRONOS_MIN_CONTEXT", "64")))
    max_context = max(min_context, int(os.getenv("CHRONOS_CONTEXT_LENGTH", "192")))
    clean = [_safe_float(x, float("nan")) for x in closes]
    clean = [x for x in clean if math.isfinite(x) and x > 0]
    if len(clean) < min_context:
        return None
    clean = clean[-max_context:]

    horizon = prediction_length or int(os.getenv("CHRONOS_PREDICTION_LENGTH", "12"))
    horizon = max(1, min(64, int(horizon)))
    pipeline = _load_pipeline()
    if pipeline is None:
        return None

    try:
        import torch

        context = torch.tensor(clean, dtype=torch.float32)
        quantiles, mean = pipeline.predict_quantiles(
            context,
            prediction_length=horizon,
            quantile_levels=[0.1, 0.5, 0.9],
        )
        q = quantiles.detach().cpu()[0]
        avg = mean.detach().cpu()[0]
        last = clean[-1]
        q10 = float(q[-1, 0])
        q50 = float(q[-1, 1])
        q90 = float(q[-1, 2])
        mean_last = float(avg[-1])

        median_return = (q50 / last - 1.0) * 100.0
        mean_return = (mean_last / last - 1.0) * 100.0
        lower_return = (q10 / last - 1.0) * 100.0
        upper_return = (q90 / last - 1.0) * 100.0
        interval = max(1e-9, q90 - q10)
        # q90-q10 is about 2.563 standard deviations for a normal distribution.
        sigma = max(interval / 2.563, last * 1e-6)
        probability_up = _normal_cdf((q50 - last) / sigma) * 100.0
        uncertainty_pct = interval / last * 100.0
        strength = min(100.0, abs(median_return) / max(uncertainty_pct, 0.05) * 50.0)

        return {
            "provider": "amazon-chronos-bolt",
            "model": os.getenv("CHRONOS_MODEL", "amazon/chronos-bolt-tiny"),
            "contextPoints": len(clean),
            "predictionLength": horizon,
            "lastPrice": round(last, 10),
            "forecastPrice": round(q50, 10),
            "meanForecastPrice": round(mean_last, 10),
            "forecastReturnPct": round(median_return, 4),
            "meanReturnPct": round(mean_return, 4),
            "lowerReturnPct": round(lower_return, 4),
            "upperReturnPct": round(upper_return, 4),
            "probabilityUp": round(max(0.0, min(100.0, probability_up)), 2),
            "probabilityDown": round(max(0.0, min(100.0, 100.0 - probability_up)), 2),
            "uncertaintyPct": round(uncertainty_pct, 4),
            "strength": round(strength, 2),
        }
    except Exception:
        logger.exception("Chronos inference failed")
        return None


def blend_signal(signal: dict[str, Any], forecast: dict[str, Any] | None) -> dict[str, Any]:
    """Blend Chronos as a bounded expert; it can never dominate the core engine."""
    if not forecast:
        return signal

    direction = str(signal.get("direction") or "").upper()
    if direction in {"LONG", "LONG_BIAS", "BUY"}:
        chronos_probability = _safe_float(forecast.get("probabilityUp"), 50.0)
        aligned_return = _safe_float(forecast.get("forecastReturnPct"))
    elif direction in {"SHORT", "SHORT_BIAS", "SELL"}:
        chronos_probability = _safe_float(forecast.get("probabilityDown"), 50.0)
        aligned_return = -_safe_float(forecast.get("forecastReturnPct"))
    else:
        chronos_probability = 50.0
        aligned_return = 0.0

    uncertainty = _safe_float(forecast.get("uncertaintyPct"), 100.0)
    max_weight = max(0.0, min(0.40, float(os.getenv("CHRONOS_MAX_WEIGHT", "0.25"))))
    # Wider forecast intervals reduce influence automatically.
    reliability = max(0.15, min(1.0, 2.0 / max(uncertainty, 0.25)))
    weight = max_weight * reliability

    old_probability = _safe_float(signal.get("probability"), 50.0)
    blended = old_probability * (1.0 - weight) + chronos_probability * weight
    # Prevent a weak/contradictory point forecast from inflating confidence.
    agreement = aligned_return > 0
    old_confidence = _safe_float(signal.get("confidence"), 50.0)
    confidence_delta = (6.0 * reliability) if agreement else (-8.0 * reliability)

    signal["probabilityBeforeChronos"] = round(old_probability, 2)
    signal["probability"] = round(max(5.0, min(95.0, blended)), 2)
    signal["confidence"] = round(max(5.0, min(95.0, old_confidence + confidence_delta)), 2)
    signal["chronos"] = {**forecast, "weight": round(weight, 4), "directionAgreement": agreement}
    profile = signal.get("tradeProfile")
    if isinstance(profile, dict):
        profile["chronos"] = round(chronos_probability, 2)
        profile["chronosWeight"] = round(weight, 4)
        profile["probability"] = signal["probability"]
        profile["confidence"] = signal["confidence"]
    return signal
