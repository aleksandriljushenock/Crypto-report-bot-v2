"""Compatibility runtime for the retired champion.pkl model.

Predictions now come from the active V14 model so legacy imports cannot diverge
from the model used by the production scoring pipeline.
"""
from __future__ import annotations
from typing import Any
from ai_score_engine import DEFAULT_WEIGHTS
from learning_engine_v14 import active_model, calibrated_probability, classify_regime, specialist_weights

class CloudModelRuntime:
    def __init__(self, refresh_seconds: int = 1800) -> None:
        self.refresh_seconds = refresh_seconds
    def refresh(self) -> None:
        return None
    def predict(self, features: dict[str, float]) -> dict[str, Any]:
        try:
            model = active_model(DEFAULT_WEIGHTS)
            regime = classify_regime(features)
            weights = specialist_weights(model, regime, str(features.get("direction") or ""))
            denom = sum(max(0.01, float(weights.get(k, DEFAULT_WEIGHTS[k]))) for k in DEFAULT_WEIGHTS)
            score = sum(float(features.get(k, 50)) * float(weights.get(k, DEFAULT_WEIGHTS[k])) for k in DEFAULT_WEIGHTS) / denom
            prob, unc = calibrated_probability(score, regime, model, str(features.get("direction") or ""))
            return {"available": True, "probability": prob, "confidence": max(0.0, 1.0-float(unc)), "model_version": model.get("version")}
        except Exception:
            return {"available": False, "probability": 0.5, "confidence": 0.0}

runtime_model = CloudModelRuntime()
