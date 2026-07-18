"""Resilient RSS news intelligence: deduplication, clustering, sentiment and impact."""
from __future__ import annotations
import hashlib, html, re
from collections import defaultdict
from typing import Any, Dict, Iterable, List

POSITIVE = {"approval", "approved", "adoption", "launch", "partnership", "inflow", "surge", "upgrade", "bullish"}
NEGATIVE = {"hack", "exploit", "lawsuit", "ban", "outflow", "liquidation", "fraud", "bearish", "shutdown"}
TOPICS = {
    "regulation": {"sec", "regulation", "lawsuit", "ban", "approval"},
    "etf": {"etf", "blackrock", "fidelity", "inflow", "outflow"},
    "security": {"hack", "exploit", "breach", "stolen"},
    "exchange": {"binance", "coinbase", "kraken", "listing"},
    "macro": {"fed", "inflation", "rates", "cpi", "jobs"},
}

def normalize_title(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()

def fingerprint(title: str) -> str:
    normalized = re.sub(r"[^a-z0-9 ]", "", normalize_title(title).lower())
    return hashlib.sha256(normalized.encode()).hexdigest()

def sentiment(title: str) -> float:
    words = set(re.findall(r"[a-z]+", title.lower()))
    pos, neg = len(words & POSITIVE), len(words & NEGATIVE)
    return round(max(-1.0, min(1.0, (pos-neg) / max(1, pos+neg))), 3)

def topic(title: str) -> str:
    words = set(re.findall(r"[a-z]+", title.lower()))
    ranked = sorted(((len(words & keys), name) for name, keys in TOPICS.items()), reverse=True)
    return ranked[0][1] if ranked and ranked[0][0] else "general"

def impact_score(title: str, source_weight: float = 1.0) -> float:
    s = abs(sentiment(title))
    high = sum(1 for keys in TOPICS.values() for key in keys if key in title.lower())
    return round(min(100.0, (35 + high*8 + s*25) * max(.5, min(1.5, source_weight))), 2)

def enrich(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique = {}
    for item in items:
        title = normalize_title(str(item.get("title") or ""))
        if not title: continue
        fp = fingerprint(title)
        if fp in unique: continue
        row = dict(item); row.update({"title": title, "fingerprint": fp, "sentiment": sentiment(title), "topic": topic(title), "impact": impact_score(title)})
        unique[fp] = row
    return sorted(unique.values(), key=lambda x: x["impact"], reverse=True)

def clusters(items: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    result = defaultdict(list)
    for item in enrich(items): result[item["topic"]].append(item)
    return dict(result)
