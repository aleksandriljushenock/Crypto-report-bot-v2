from adaptive_weights import get_adaptive_weights
from alpha_engine_v2 import calculate_alpha_v2, clamp


def _normalize_component(value, maximum):
    if maximum <= 0:
        return 0
    return max(0, min(1, float(value or 0) / maximum))


def calculate_alpha_v3(raw_research, research, security, discovery, intelligence):
    base = calculate_alpha_v2(raw_research, research, security, discovery)
    if not base.get("available"):
        base["version"] = 3
        return base

    learned = get_adaptive_weights()
    weights = learned["weights"]
    b = base.get("components", {})
    normalized = {
        "fundamental": _normalize_component(b.get("fundamental"), 18),
        "tokenomics": _normalize_component(b.get("tokenomics"), 18),
        "development": _normalize_component(b.get("development"), 12),
        "adoption": _normalize_component(b.get("adoption"), 10),
        "narrative": _normalize_component(b.get("narrative"), 12),
        "transparency": _normalize_component(b.get("transparency"), 10),
        "marketStructure": _normalize_component(b.get("marketStructure"), 15),
        "security": _normalize_component(b.get("security"), 10),
        "dataQuality": _normalize_component(b.get("dataQuality"), 5),
        "vc": _normalize_component(intelligence.get("vc", {}).get("score"), 15),
        "unlocks": _normalize_component(intelligence.get("unlocks", {}).get("score"), 10),
        "social": _normalize_component(intelligence.get("social", {}).get("score"), 10),
        "smartMoney": _normalize_component(intelligence.get("smartMoney", {}).get("score"), 10),
    }
    score = round(sum(normalized[key] * weights[key] for key in weights))
    hard_reject = bool(base.get("hardReject")) or bool(intelligence.get("unlocks", {}).get("hardReject"))
    if hard_reject:
        score = min(score, 49)
    score = clamp(score)

    if hard_reject:
        action, label = "REJECT", "🔴 НЕ ПОКУПАТЬ"
    elif score >= 82:
        action, label = "HIGH_PRIORITY", "🟢 ВЫСОКИЙ ПРИОРИТЕТ"
    elif score >= 68:
        action, label = "WATCH", "🟡 ИЗУЧАТЬ ПЕРЕД ЛИСТИНГОМ"
    elif score >= 55:
        action, label = "RESEARCH_MORE", "🟡 НУЖНО БОЛЬШЕ ДАННЫХ"
    else:
        action, label = "SKIP", "🔴 ПРОПУСТИТЬ"

    reasons_for = list(base.get("reasonsFor", []))
    reasons_against = list(base.get("reasonsAgainst", []))
    vc = intelligence.get("vc", {})
    if vc.get("investors"):
        reasons_for.append("Обнаружены известные инвесторы: " + ", ".join(x["name"] for x in vc["investors"][:3]))
    reasons_against.extend(intelligence.get("unlocks", {}).get("risks", []))
    if intelligence.get("social", {}).get("growthPercent"):
        reasons_for.append("Есть измеримая динамика social/developer метрик")

    provider_coverage = sum(1 for key in ("vc", "unlocks", "social", "smartMoney") if intelligence.get(key, {}).get("available"))
    confidence = min(95, round(float(base.get("confidence", 0)) * 0.8 + provider_coverage * 5))
    return {
        **base,
        "version": 3,
        "score": score,
        "grade": "A" if score >= 85 else "B" if score >= 75 else "C" if score >= 65 else "D" if score >= 50 else "F",
        "confidence": confidence,
        "interesting": action in {"HIGH_PRIORITY", "WATCH"} and not hard_reject,
        "hardReject": hard_reject,
        "action": action,
        "actionLabel": label,
        "intelligence": intelligence,
        "adaptiveWeights": learned,
        "weightedComponents": {key: round(normalized[key] * weights[key], 2) for key in weights},
        "reasonsFor": reasons_for[:12],
        "reasonsAgainst": reasons_against[:12],
    }
