"""Cached runtime reader for v18 adaptive champion model."""
from __future__ import annotations
import math, os, time
from typing import Any, Dict, Optional

_CACHE: Dict[str, Any] = {"at": 0.0, "model": None, "version": None}


def _enabled() -> bool:
    return str(os.getenv("ADAPTIVE_MODEL_ENABLED", "true")).lower() in {"1","true","yes","on"}


def _load() -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not _enabled(): return None, None
    ttl = max(30, int(float(os.getenv("ADAPTIVE_MODEL_CACHE_SECONDS", "300"))))
    now = time.time()
    if now - _CACHE["at"] < ttl:
        return _CACHE["model"], _CACHE["version"]
    try:
        from cloud_client import get_supabase_client
        rows = (get_supabase_client().table("adaptive_model_versions")
                .select("version,model_json").eq("status", "champion").order("activated_at", desc=True).limit(1).execute().data or [])
        if rows:
            _CACHE.update(at=now, model=rows[0].get("model_json"), version=rows[0].get("version"))
        else:
            _CACHE.update(at=now, model=None, version=None)
    except Exception:
        _CACHE["at"] = now
    return _CACHE["model"], _CACHE["version"]


def _num(v, d=0.0):
    try:return float(v)
    except:return d


def predict(signal: Dict[str, Any], quality: float, probability: float, ev: float) -> Dict[str, Any]:
    model, version = _load()
    if not model: return {"available": False}
    f = signal.get("aiFactors") or {}
    venues = signal.get("exchangeCoverage") or signal.get("venues") or []
    coverage = len(venues) if isinstance(venues, list) else (_num(venues.get("count"),1) if isinstance(venues,dict) else _num(signal.get("exchangeCoverageCount"),1))
    vals = [quality, probability, ev, _num(signal.get("aiScore") or signal.get("score"),50), _num(signal.get("rr"),1), coverage,
            _num(f.get("trend"),50),_num(f.get("volume"),50),_num(f.get("momentum"),50),_num(f.get("alignment"),50),_num(f.get("capital_flow"),50),_num(f.get("smart_money"),50)]
    try:
        z = float(model.get("bias",0)); means=model["means"]; stds=model["stds"]; weights=model["weights"]
        for i,v in enumerate(vals): z += float(weights[i]) * ((v-float(means[i]))/max(float(stds[i]),1e-6))
        p = 1/(1+math.exp(-max(-60,min(60,z))))
        return {"available":True,"version":version,"probability":round(p*100,2)}
    except Exception:
        return {"available":False}
