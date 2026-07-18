import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DATABASE_PATH = Path("data") / "intelligence_history.db"

TOP_TIER_VC = {
    "a16z": 10, "andreessen horowitz": 10, "paradigm": 10,
    "polychain": 9, "dragonfly": 9, "multicoin": 9, "pantera": 9,
    "framework": 8, "binance labs": 9, "coinbase ventures": 8,
    "jump crypto": 8, "hashed": 8, "animoca": 7, "delphi digital": 8,
    "mechanism capital": 7, "variant": 8, "electric capital": 8,
    "the block chain group": 5, "okx ventures": 7, "kucoin ventures": 6,
    "mexc ventures": 5, "gate ventures": 5, "spartan group": 7,
}

MARKET_MAKERS = (
    "wintermute", "jump", "gsr", "amber group", "dwf labs",
    "falconx", "cumberland", "keyrock", "flow traders",
)


def _connect():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS social_snapshots (
            project_key TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            twitter_followers INTEGER,
            reddit_subscribers INTEGER,
            github_stars INTEGER,
            github_forks INTEGER,
            github_contributors INTEGER,
            PRIMARY KEY(project_key, captured_at)
        )
        """
    )
    return conn


def _safe_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _text(raw_research, research):
    cg = raw_research.get("coingecko", {}) or {}
    values = [
        cg.get("description", ""),
        " ".join(cg.get("categories", []) or []),
        " ".join(str(x) for x in research.get("positives", []) or []),
        json.dumps(raw_research, ensure_ascii=False),
    ]
    return re.sub(r"\s+", " ", " ".join(values)).lower()


def analyze_vc(raw_research, research):
    text = _text(raw_research, research)
    matches = []
    for name, weight in TOP_TIER_VC.items():
        if name in text:
            matches.append({"name": name.title(), "weight": weight})
    matches.sort(key=lambda x: x["weight"], reverse=True)
    score = min(15, sum(x["weight"] for x in matches[:3]) * 0.6)
    return {
        "available": bool(matches),
        "score": round(score, 2),
        "investors": matches[:8],
        "confidence": 55 if matches else 0,
        "note": "Эвристика по публичным описаниям; подключаемый платный провайдер повысит точность.",
    }


def analyze_unlocks(research):
    metrics = research.get("metrics", {}) or {}
    fdv_ratio = metrics.get("fdvToMarketCap")
    circulating = metrics.get("circulatingRatio")
    risks = []
    score = 5
    hard_reject = False
    if circulating is None:
        risks.append("Доля circulating supply неизвестна")
    else:
        circulating = float(circulating)
        if circulating < 0.10:
            score = 0
            hard_reject = True
            risks.append("В обращении менее 10% предложения")
        elif circulating < 0.25:
            score = 2
            risks.append("Высокий будущий dilution: в обращении менее 25%")
        elif circulating < 0.50:
            score = 5
        else:
            score = 8
    if fdv_ratio is not None and float(fdv_ratio) >= 5:
        score = max(0, score - 4)
        risks.append("FDV/Market Cap >= 5")
        hard_reject = True
    return {
        "available": circulating is not None,
        "score": score,
        "hardReject": hard_reject,
        "risks": risks,
        "nextUnlock": None,
        "note": "Прокси-оценка dilution; точный календарь требует внешнего unlock-провайдера.",
    }


def analyze_social(project_key, raw_research):
    cg = raw_research.get("coingecko", {}) or {}
    community = cg.get("community", {}) or {}
    repositories = raw_research.get("github", []) or []
    github_stars = sum(_safe_int(repo.get("stars")) or 0 for repo in repositories if isinstance(repo, dict))
    github_forks = sum(_safe_int(repo.get("forks")) or 0 for repo in repositories if isinstance(repo, dict))
    github_contributors = sum(
        _safe_int((repo.get("contributors") or {}).get("countReturned")) or 0
        for repo in repositories if isinstance(repo, dict)
    )
    snapshot = {
        "twitter_followers": _safe_int(community.get("twitterFollowers")),
        "reddit_subscribers": _safe_int(community.get("redditSubscribers")),
        "github_stars": github_stars or None,
        "github_forks": github_forks or None,
        "github_contributors": github_contributors or None,
    }
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        previous = conn.execute(
            "SELECT * FROM social_snapshots WHERE project_key=? ORDER BY captured_at DESC LIMIT 1",
            (project_key,),
        ).fetchone()
        conn.execute(
            """INSERT OR REPLACE INTO social_snapshots
            (project_key,captured_at,twitter_followers,reddit_subscribers,github_stars,github_forks,github_contributors)
            VALUES (?,?,?,?,?,?,?)""",
            (project_key, now, snapshot["twitter_followers"], snapshot["reddit_subscribers"],
             snapshot["github_stars"], snapshot["github_forks"], snapshot["github_contributors"]),
        )
    growth = {}
    if previous:
        for key in snapshot:
            old = previous[key]
            new = snapshot[key]
            if old not in (None, 0) and new is not None:
                growth[key] = round((new - old) / old * 100, 2)
    score = 0
    if snapshot["twitter_followers"]:
        score += min(5, 1 + snapshot["twitter_followers"] / 100000)
    if snapshot["github_stars"]:
        score += min(3, snapshot["github_stars"] / 1000)
    if any(v > 5 for v in growth.values()):
        score += 2
    return {
        "available": any(v is not None for v in snapshot.values()),
        "score": round(min(10, score), 2),
        "snapshot": snapshot,
        "growthPercent": growth,
        "note": "Рост становится информативным после накопления нескольких снимков.",
    }


def analyze_smart_money(raw_research):
    text = _text(raw_research, {})
    detected = [name.title() for name in MARKET_MAKERS if name in text]
    return {
        "available": bool(detected),
        "score": min(10, len(detected) * 3),
        "detected": detected,
        "note": "Без on-chain провайдера это только упоминания в публичных данных, не подтвержденные держатели.",
    }


def collect_intelligence(project_key, raw_research, research):
    return {
        "vc": analyze_vc(raw_research, research),
        "unlocks": analyze_unlocks(research),
        "social": analyze_social(project_key, raw_research),
        "smartMoney": analyze_smart_money(raw_research),
    }
