from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, log_loss
from supabase import Client, create_client

from cloud_model_store import load_model, save_model


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cloud-training")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
)


def load_training_rows(
    limit: int = 5000,
) -> list[dict[str, Any]]:
    response = (
        supabase.table("learning_observations")
        .select(
            "id, features, real_result, created_at"
        )
        .not_.is_("real_result", "null")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )

    return response.data or []


def extract_target(real_result: Any) -> int | None:
    if isinstance(real_result, str):
        try:
            real_result = json.loads(real_result)
        except json.JSONDecodeError:
            return None

    if not isinstance(real_result, dict):
        return None

    value = real_result.get("target")

    if value is None:
        value = real_result.get("success")

    if isinstance(value, bool):
        return int(value)

    try:
        value = int(value)
    except (TypeError, ValueError):
        return None

    if value not in (0, 1):
        return None

    return value


def prepare_dataset(
    rows: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    parsed: list[tuple[dict[str, float], int]] = []
    feature_names: set[str] = set()

    for row in rows:
        features = row.get("features")
        target = extract_target(
            row.get("real_result")
        )

        if isinstance(features, str):
            try:
                features = json.loads(features)
            except json.JSONDecodeError:
                continue

        if not isinstance(features, dict):
            continue

        if target is None:
            continue

        clean_features: dict[str, float] = {}

        for key, value in features.items():
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue

            if not np.isfinite(numeric):
                continue

            clean_features[str(key)] = numeric
            feature_names.add(str(key))

        if clean_features:
            parsed.append((clean_features, target))

    ordered_features = sorted(feature_names)

    matrix: list[list[float]] = []
    targets: list[int] = []

    for features, target in parsed:
        matrix.append([
            features.get(name, 0.0)
            for name in ordered_features
        ])
        targets.append(target)

    if not matrix:
        raise RuntimeError(
            "Нет пригодных размеченных примеров"
        )

    return (
        np.asarray(matrix, dtype=np.float64),
        np.asarray(targets, dtype=np.int64),
        ordered_features,
    )


def create_model() -> SGDClassifier:
    return SGDClassifier(
        loss="log_loss",
        penalty="elasticnet",
        alpha=0.0001,
        random_state=42,
        max_iter=1000,
        tol=1e-3,
    )


def train() -> None:
    rows = load_training_rows()

    if len(rows) < 30:
        logger.info(
            "Недостаточно данных: %s из 30",
            len(rows),
        )
        return

    x, y, feature_names = prepare_dataset(rows)

    artifact = load_model(
        "champion.pkl",
        default=None,
    )

    model: SGDClassifier

    if (
        isinstance(artifact, dict)
        and artifact.get("feature_names")
        == feature_names
        and artifact.get("model") is not None
    ):
        model = artifact["model"]
        logger.info("Продолжаем обучение модели")

    else:
        model = create_model()
        logger.info("Создаём новую модель")

    model.partial_fit(
        x,
        y,
        classes=np.asarray([0, 1]),
    )

    probability = model.predict_proba(x)[:, 1]
    prediction = (probability >= 0.5).astype(int)

    metrics = {
        "accuracy": float(
            accuracy_score(y, prediction)
        ),
        "log_loss": float(
            log_loss(
                y,
                probability,
                labels=[0, 1],
            )
        ),
        "samples": int(len(y)),
    }

    version = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S")

    new_artifact = {
        "model": model,
        "feature_names": feature_names,
        "metrics": metrics,
        "version": version,
        "trained_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    save_model(
        new_artifact,
        f"checkpoints/{version}.pkl",
    )

    old_loss = float("inf")

    if isinstance(artifact, dict):
        old_metrics = artifact.get("metrics") or {}

        try:
            old_loss = float(
                old_metrics.get(
                    "log_loss",
                    float("inf"),
                )
            )
        except (TypeError, ValueError):
            pass

    promote = (
        artifact is None
        or metrics["log_loss"] <= old_loss
        or metrics["samples"]
        > int(
            (artifact.get("metrics") or {})
            .get("samples", 0)
        )
    )

    save_model(
        new_artifact,
        "latest.pkl",
    )

    if promote:
        save_model(
            new_artifact,
            "champion.pkl",
        )

    supabase.table("training_runs").insert({
        "model_version": version,
        "metrics": metrics,
        "feature_importance": {},
        "samples_count": metrics["samples"],
        "status": (
            "champion"
            if promote
            else "challenger"
        ),
    }).execute()

    logger.info(
        "Обучение завершено: %s",
        metrics,
    )


if __name__ == "__main__":
    train()