"""Cached runtime reader for v18 adaptive champion model."""
from __future__ import annotations
import math, os, time
from typing import Any, Dict, Optional

_CACHE: Dict[str, Any] = {"at": 0.0, "model": None, "version": None}


def _enabled() -> bool:
    return str(os.getenv("ADAPTIVE_MODEL_ENABLED", "true")).lower() in {"1","true","yes","on"}


def _load() -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not _enabled(): return None, None
    # V52: Paper-trained probability is shadow-only until the execution ledger
    # is large enough to support a stable chronological validation.
    try:
        from repositories.paper_repository import PaperRepository
        minimum = max(1, int(float(os.getenv("ADAPTIVE_MODEL_RUNTIME_MIN_TRADES", "200"))))
        if len(PaperRepository().valid_closed_positions(minimum, ascending=False)) < minimum:
            return None, None
    except Exception:
        return None, None
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



def invalidate_cache() -> None:
    _CACHE.update(at=0.0, model=None, version=None)

def _num(v, d=0.0):
    try:return float(v)
    except:return d


def predict(signal: Dict[str, Any], quality: float, probability: float, ev: float) -> Dict[str, Any]:
    model, version = _load()
    if not model: return {"available": False}
    f = signal.get("aiFactors") or {}; venues=signal.get("marketExchanges") or signal.get("exchangeCoverage") or signal.get("venues") or []
    coverage=len(venues) if isinstance(venues,list) else (_num(venues.get("count"),1) if isinstance(venues,dict) else _num(signal.get("exchangeCount") or signal.get("exchangeCoverageCount"),1))
    direction=str(signal.get("direction") or "").upper(); setup=str(signal.get("setup") or "").upper(); regime=str(signal.get("marketRegime") or signal.get("aiRegime") or "").lower(); rel=signal.get("reliability") or {}; rel_score=_num(rel.get("score") if isinstance(rel,dict) else rel,70)
    values={
      "quality":quality,"probability":probability,"ev":ev,"score":_num(signal.get("aiScore") or signal.get("score"),50),"rr":_num(signal.get("rr"),1),"coverage":coverage,
      "uncertainty":_num(signal.get("uncertainty") or signal.get("aiUncertainty"),50),"reliability":rel_score,
      "trend":_num(f.get("trend"),50),"volume":_num(f.get("volume"),50),"momentum":_num(f.get("momentum"),50),"alignment":_num(f.get("alignment"),50),"capital_flow":_num(f.get("capital_flow"),50),"smart_money":_num(f.get("smart_money"),50),"open_interest":_num(f.get("open_interest"),50),
      "is_short":1.0 if "SHORT" in direction else 0.0,"is_breakout":1.0 if setup=="BREAKOUT" else 0.0,"is_pullback":1.0 if setup=="PULLBACK" else 0.0,
      "regime_bull":1.0 if "bull" in regime else 0.0,"regime_bear":1.0 if "bear" in regime else 0.0,"regime_range":1.0 if "range" in regime else 0.0,
    }
    try:
        names=list(model.get("features") or [])
        if not names: return {"available":False}
        means=model["means"]; stds=model["stds"]; weights=model["weights"]
        if not (len(names)==len(means)==len(stds)==len(weights)): return {"available":False}
        z=float(model.get("bias",0))
        for i,name in enumerate(names):
            v=float(values.get(name,50.0)); z += float(weights[i])*((v-float(means[i]))/max(float(stds[i]),1e-6))
        p=1/(1+math.exp(-max(-60,min(60,z))))
        return {"available":True,"version":version,"probability":round(p*100,2),"featureSchema":names}
    except Exception:
        return {"available":False}

