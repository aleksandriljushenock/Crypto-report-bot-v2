import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.logging_setup import get_logger

_BASE = Path(__file__).resolve().parent
_DATA = _BASE / "data"
_DATA.mkdir(exist_ok=True)
_LAST = _DATA / "last_scan_intelligence.json"
_HISTORY = _DATA / "scan_intelligence_history.jsonl"
_LOCK = threading.Lock()
_PROCESS_STARTED_AT = datetime.now(timezone.utc)
_LOG = get_logger("scanner_intelligence")
_CLOUD_WARNED = False


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def process_started_at():
    return _PROCESS_STARTED_AT.isoformat()


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _parse_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _cloud_enabled():
    value = os.getenv("SCANNER_STATE_CLOUD_ENABLED", "true").strip().lower()
    return value in {"1", "true", "yes", "on"} and bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_SERVICE_KEY"))


def _cloud_client():
    from cloud_client import get_supabase_client
    return get_supabase_client()


def _warn_cloud_once(message, exc):
    global _CLOUD_WARNED
    if not _CLOUD_WARNED:
        _LOG.warning("%s: %s", message, exc)
        _CLOUD_WARNED = True


def _save_cloud(row):
    if not _cloud_enabled():
        return
    try:
        now = _now_iso()
        run_time = row.get("runTimeUtc") or row.get("savedAt") or now
        client = _cloud_client()
        client.table("scanner_runtime_state").upsert({
            "state_key": "trade_scanner",
            "payload": row,
            "last_success_at": run_time,
            "updated_at": now,
        }, on_conflict="state_key").execute()
        stages = row.get("stages") or {}
        client.table("scanner_scan_history").insert({
            "run_time_utc": run_time,
            "rows_analyzed": int(stages.get("analyzed") or 0),
            "signals_count": int(stages.get("signals") or 0),
            "payload": row,
        }).execute()
    except Exception as exc:
        _warn_cloud_once("Scanner state cloud save skipped", exc)


def _load_cloud_last():
    if not _cloud_enabled():
        return {}
    try:
        response = (
            _cloud_client().table("scanner_runtime_state")
            .select("payload,last_success_at,updated_at")
            .eq("state_key", "trade_scanner")
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return {}
        payload = dict(rows[0].get("payload") or {})
        if payload:
            payload.setdefault("savedAt", rows[0].get("updated_at"))
            payload["restoredFromCloud"] = True
        return payload
    except Exception as exc:
        _warn_cloud_once("Scanner state cloud load skipped", exc)
        return {}


def _load_cloud_history(hours=24, limit=120):
    if not _cloud_enabled():
        return []
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=float(hours))).isoformat()
        response = (
            _cloud_client().table("scanner_scan_history")
            .select("payload,run_time_utc")
            .gte("run_time_utc", cutoff)
            .order("run_time_utc", desc=True)
            .limit(max(1, int(limit)))
            .execute()
        )
        out = []
        for item in response.data or []:
            row = dict(item.get("payload") or {})
            row.setdefault("runTimeUtc", item.get("run_time_utc"))
            if row:
                out.append(row)
        return list(reversed(out))
    except Exception as exc:
        _warn_cloud_once("Scanner history cloud load skipped", exc)
        return []


def save_scan_intelligence(payload):
    row = dict(payload or {})
    row.setdefault("savedAt", _now_iso())
    with _LOCK:
        _LAST.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        with _HISTORY.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        _trim_history_locked()
    _save_cloud(row)
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


def _load_local_last():
    try:
        return json.loads(_LAST.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_last_scan_intelligence():
    local = _load_local_last()
    cloud = _load_cloud_last()
    if not local:
        return cloud
    if not cloud:
        return local
    local_dt = _parse_dt(local.get("runTimeUtc") or local.get("savedAt"))
    cloud_dt = _parse_dt(cloud.get("runTimeUtc") or cloud.get("savedAt"))
    if cloud_dt and (not local_dt or cloud_dt > local_dt):
        return cloud
    return local


def is_previous_process_snapshot(row=None):
    row = row or get_last_scan_intelligence()
    if not row:
        return False
    dt = _parse_dt(row.get("runTimeUtc") or row.get("savedAt"))
    return bool(dt and dt < _PROCESS_STARTED_AT)


def _load_local_history(hours=24, limit=120):
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
                dt = _parse_dt(ts)
                if dt and dt >= cutoff:
                    out.append(row)
            except Exception:
                continue
    except Exception:
        return []
    return out


def get_scan_history(hours=24, limit=120):
    local = _load_local_history(hours=hours, limit=limit)
    cloud = _load_cloud_history(hours=hours, limit=limit)
    merged = {}
    for row in cloud + local:
        key = str(row.get("runTimeUtc") or row.get("savedAt") or len(merged))
        merged[key] = row
    rows = list(merged.values())
    rows.sort(key=lambda r: str(r.get("runTimeUtc") or r.get("savedAt") or ""))
    return rows[-max(1, int(limit)):]


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
