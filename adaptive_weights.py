import json
import sqlite3
from pathlib import Path


DATABASE_PATH = Path("data") / "alpha_outcomes.db"
DEFAULT_WEIGHTS = {
    "fundamental": 16, "tokenomics": 14, "development": 9,
    "adoption": 8, "narrative": 9, "transparency": 7,
    "marketStructure": 10, "security": 8, "dataQuality": 4,
    "vc": 6, "unlocks": 5, "social": 3, "smartMoney": 1,
}


def get_adaptive_weights(min_samples=30):
    if not DATABASE_PATH.exists():
        return {"weights": DEFAULT_WEIGHTS, "learned": False, "samples": 0}
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT p.components_json, o.return_percent FROM predictions p
        JOIN outcomes o ON p.project_key=o.project_key WHERE o.horizon='7d'"""
    ).fetchall()
    conn.close()
    if len(rows) < min_samples:
        return {"weights": DEFAULT_WEIGHTS, "learned": False, "samples": len(rows)}
    # Robust, dependency-free directional adjustment based on component/return covariance.
    components = {key: [] for key in DEFAULT_WEIGHTS}
    returns = []
    for row in rows:
        data = json.loads(row["components_json"] or "{}")
        returns.append(float(row["return_percent"]))
        for key in components:
            components[key].append(float(data.get(key, 0) or 0))
    mean_r = sum(returns) / len(returns)
    raw = {}
    for key, values in components.items():
        mean_x = sum(values) / len(values)
        cov = sum((x - mean_x) * (r - mean_r) for x, r in zip(values, returns))
        raw[key] = max(0.2, DEFAULT_WEIGHTS[key] * (1 + max(-0.35, min(0.35, cov / (len(values) * 100 + 1)))))
    scale = 100 / sum(raw.values())
    weights = {key: round(value * scale, 2) for key, value in raw.items()}
    return {"weights": weights, "learned": True, "samples": len(rows)}
