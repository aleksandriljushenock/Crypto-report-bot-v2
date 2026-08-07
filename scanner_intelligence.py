import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parent
_DATA = _BASE / "data"
_DATA.mkdir(exist_ok=True)
_LAST = _DATA / "last_scan_intelligence.json"
_HISTORY = _DATA / "scan_intelligence_history.jsonl"
_LOCK = threading.Lock()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def save_scan_intelligence(payload):
    row = dict(payload or {})
    row.setdefault("savedAt", _now_iso())
    with _LOCK:
        _LAST.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        with _HISTORY.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        _trim_history_locked()
    return row


def _trim_history_locked(max_rows=None):
    max_rows = int(max_rows or os.getenv("SCANNER_INTELLIGENCE_HISTORY_LIMIT", "300"))
    if not _HISTORY.exists():
        return
    try:
        lines = _HISTORY.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_rows:
            _HISTORY.write_text("\n".join(lines[-max_rows:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def get_last_scan_intelligence():
    try:
        return json.loads(_LAST.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_scan_history(hours=24, limit=120):
    if not _HISTORY.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=float(hours))
    out = []
    try:
        lines = _HISTORY.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]
        for line in lines:
            try:
                row = json.loads(line)
                ts = row.get("runTimeUtc") or row.get("savedAt")
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt >= cutoff:
                    out.append(row)
            except Exception:
                continue
    except Exception:
        return []
    return out


def aggregate_24h():
    rows = get_scan_history(hours=24, limit=300)
    totals = {
        "scans": len(rows), "analyzed": 0, "status": 0, "score": 0,
        "rr": 0, "probability": 0, "quality": 0, "ev": 0, "signals": 0,
    }
    for row in rows:
        st = row.get("stages") or {}
        totals["analyzed"] += int(st.get("analyzed") or 0)
        for key in ("status", "score", "rr", "probability", "quality", "ev", "signals"):
            totals[key] += int(st.get(key) or 0)
    return totals


def build_recommendation(summary):
    st = (summary or {}).get("stages") or {}
    analyzed = max(1, int(st.get("analyzed") or 0))
    prob = int(st.get("probability") or 0)
    quality = int(st.get("quality") or 0)
    ev = int(st.get("ev") or 0)
    signals = int(st.get("signals") or 0)
    if signals:
        return "Фильтры дали финальный сигнал — менять пороги по одному скану не требуется."
    if prob == 0 and analyzed >= 10:
        return "Главный bottleneck сейчас — Probability. Не меняй порог до нескольких сканов; сначала накопи статистику 24ч."
    if prob > 0 and quality == 0:
        return "Кандидаты доходят до AI, но не проходят Quality. Следи за Quality distribution; порог пока не меняй."
    if quality > 0 and ev == 0:
        return "Quality проходит, но EV блокирует сделки. Это полезный фильтр; пересматривать его стоит только по серии сканов."
    return "Сигналов нет, но одного скана недостаточно для изменения стратегии. Используй статистику за 24 часа."


def compact_near_misses(items, limit=5):
    cleaned = []
    for item in items or []:
        cleaned.append({
            "symbol": item.get("symbol"),
            "reason": item.get("reason") or item.get("reasons"),
            "score": _safe_float(item.get("score")),
            "rr": _safe_float(item.get("rr")),
            "probability": _safe_float(item.get("probability")),
            "quality": _safe_float(item.get("qualityScore")),
            "ev": _safe_float(item.get("expectedValuePct")),
        })
    return cleaned[:max(1, int(limit))]
