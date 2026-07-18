from __future__ import annotations

import time
from threading import Lock
from typing import Any

import numpy as np

from cloud_model_store import load_model


class CloudModelRuntime:
    def __init__(
        self,
        refresh_seconds: int = 1800,
    ) -> None:
        self.refresh_seconds = refresh_seconds
        self._artifact: dict[str, Any] | None = None
        self._loaded_at = 0.0
        self._lock = Lock()

    def refresh(self) -> None:
        with self._lock:
            artifact = load_model(
                "champion.pkl",
                default=None,
            )

            if isinstance(artifact, dict):
                self._artifact = artifact
                self._loaded_at = time.time()

    def predict(
        self,
        features: dict[str, float],
    ) -> dict[str, Any]:
        if (
            self._artifact is None
            or time.time() - self._loaded_at
            > self.refresh_seconds
        ):
            self.refresh()

        if not self._artifact:
            return {
                "available": False,
                "probability": 0.5,
                "confidence": 0.0,
            }

        model = self._artifact["model"]
        feature_names = self._artifact[
            "feature_names"
        ]

        vector = np.asarray([[
            float(features.get(name, 0.0))
            for name in feature_names
        ]])

        probability = float(
            model.predict_proba(vector)[0, 1]
        )

        return {
            "available": True,
            "probability": probability,
            "confidence": abs(
                probability - 0.5
            ) * 200,
            "model_version":
                self._artifact.get("version"),
        }


runtime_model = CloudModelRuntime()