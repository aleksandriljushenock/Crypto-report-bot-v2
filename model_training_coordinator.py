"""V48 training coordinator: local lock + host flock + fenced Supabase lease."""
from __future__ import annotations
import os, socket, threading, time, uuid
from contextlib import contextmanager
from pathlib import Path
try:
    import fcntl
except ImportError:
    fcntl=None

_LOCAL=threading.Lock()
_LOCK_PATH=Path(os.getenv("MODEL_TRAINING_LOCK_FILE","data/model_training.lock"))
_STATE_LOCK=threading.Lock()
_ACTIVE=None


def _enabled():
    return bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_SERVICE_KEY")) and os.getenv("MODEL_TRAINING_DISTRIBUTED_LOCK","true").lower() not in {"0","false","no","off"}

def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()

def _ttl(): return max(180,int(os.getenv("MODEL_TRAINING_LEASE_SECONDS","900")))

def _acquire(token):
    if not _enabled(): return True,0
    try:
        data=_client().rpc("model_training_lease_acquire_v48",{"p_lock_name":"v14-training","p_token":token,"p_owner":socket.gethostname(),"p_ttl_seconds":_ttl()}).execute().data
        if isinstance(data,dict): return bool(data.get("ok")),int(data.get("generation") or 0)
        return False,0
    except Exception:
        return False,0

def _renew(token,generation):
    if not _enabled(): return True
    try:
        return bool(_client().rpc("model_training_lease_renew_v48",{"p_lock_name":"v14-training","p_token":token,"p_generation":int(generation),"p_ttl_seconds":_ttl()}).execute().data)
    except Exception:
        return False

def _release(token,generation):
    if not _enabled(): return True
    for delay in (0,1,2,4):
        if delay: time.sleep(delay)
        try:
            return bool(_client().rpc("model_training_lease_release_v48",{"p_lock_name":"v14-training","p_token":token,"p_generation":int(generation)}).execute().data)
        except Exception:
            pass
    return False

def _heartbeat(token,generation,stop,lost):
    interval=max(20,min(_ttl()//4,120))
    while not stop.wait(interval):
        ok=False
        for delay in (0,1,3):
            if delay and stop.wait(delay): return
            if _renew(token,generation): ok=True; break
        if not ok:
            lost.set()
            return

def lease_healthy()->bool:
    """False only while this process owns a distributed slot and has lost its lease."""
    with _STATE_LOCK:
        state=_ACTIVE
        if not state: return True
        return not state["lost"].is_set()

def lease_generation()->int|None:
    with _STATE_LOCK:
        return int(_ACTIVE["generation"]) if _ACTIVE else None

def lease_fence()->tuple[str|None,int|None]:
    with _STATE_LOCK:
        if not _ACTIVE:
            return None,None
        return str(_ACTIVE["token"]),int(_ACTIVE["generation"])

@contextmanager
def training_slot():
    global _ACTIVE
    if not _LOCAL.acquire(blocking=False): yield False; return
    fh=None; locked=False; token=uuid.uuid4().hex; distributed=False; generation=0
    stop=threading.Event(); lost=threading.Event(); hb=None
    try:
        if fcntl is not None:
            _LOCK_PATH.parent.mkdir(parents=True,exist_ok=True); fh=open(_LOCK_PATH,"a+")
            try: fcntl.flock(fh.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB); locked=True
            except BlockingIOError: yield False; return
        else: locked=True
        distributed,generation=_acquire(token)
        if not distributed: yield False; return
        with _STATE_LOCK:
            _ACTIVE={"token":token,"generation":generation,"lost":lost}
        if _enabled():
            hb=threading.Thread(target=_heartbeat,args=(token,generation,stop,lost),daemon=True,name="model-training-lease-heartbeat"); hb.start()
        yield True
    finally:
        stop.set()
        if hb: hb.join(timeout=2)
        if distributed: _release(token,generation)
        with _STATE_LOCK:
            _ACTIVE=None
        if locked and fh is not None and fcntl is not None:
            try: fcntl.flock(fh.fileno(),fcntl.LOCK_UN)
            except Exception: pass
        if fh:
            try: fh.close()
            except Exception: pass
        _LOCAL.release()

def training_running()->bool:
    if not _LOCAL.acquire(blocking=False): return True
    _LOCAL.release()
    if fcntl is not None:
        _LOCK_PATH.parent.mkdir(parents=True,exist_ok=True)
        with open(_LOCK_PATH,"a+") as fh:
            try: fcntl.flock(fh.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB); fcntl.flock(fh.fileno(),fcntl.LOCK_UN)
            except BlockingIOError: return True
    if _enabled():
        try: return bool(_client().rpc("model_training_lease_running_v48",{"p_lock_name":"v14-training"}).execute().data)
        except Exception: return False  # status unknown must not be displayed as a false running state
    return False
