"""V47 training coordinator: local lock + host flock + renewable Supabase lease."""
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

def _enabled():
    return bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_SERVICE_KEY")) and os.getenv("MODEL_TRAINING_DISTRIBUTED_LOCK","true").lower() not in {"0","false","no","off"}

def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()

def _ttl(): return max(300,int(os.getenv("MODEL_TRAINING_LEASE_SECONDS","1800")))

def _acquire(token):
    if not _enabled(): return True
    try:
        return bool(_client().rpc("model_training_lease_acquire_v47",{"p_lock_name":"v14-training","p_token":token,"p_owner":socket.gethostname(),"p_ttl_seconds":_ttl()}).execute().data)
    except Exception: return False

def _renew(token):
    if not _enabled(): return True
    try: return bool(_client().rpc("model_training_lease_renew_v47",{"p_lock_name":"v14-training","p_token":token,"p_ttl_seconds":_ttl()}).execute().data)
    except Exception: return False

def _release(token):
    if not _enabled(): return True
    for delay in (0,1,2):
        if delay: time.sleep(delay)
        try: return bool(_client().rpc("model_training_lease_release_v47",{"p_lock_name":"v14-training","p_token":token}).execute().data)
        except Exception: pass
    return False

def _heartbeat(token, stop):
    interval=max(30,min(_ttl()//3,300))
    while not stop.wait(interval):
        if not _renew(token):
            # Fail-safe: do not silently claim the lease was renewed. Training code
            # still holds local locks; remote lease expiry is visible in status.
            return

@contextmanager
def training_slot():
    if not _LOCAL.acquire(blocking=False): yield False; return
    fh=None; locked=False; token=uuid.uuid4().hex; distributed=False; stop=threading.Event(); hb=None
    try:
        if fcntl is not None:
            _LOCK_PATH.parent.mkdir(parents=True,exist_ok=True); fh=open(_LOCK_PATH,"a+")
            try: fcntl.flock(fh.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB); locked=True
            except BlockingIOError: yield False; return
        else: locked=True
        distributed=_acquire(token)
        if not distributed: yield False; return
        if _enabled():
            hb=threading.Thread(target=_heartbeat,args=(token,stop),daemon=True); hb.start()
        yield True
    finally:
        stop.set()
        if hb: hb.join(timeout=1)
        if distributed: _release(token)
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
        try: return bool(_client().rpc("model_training_lease_running_v47",{"p_lock_name":"v14-training"}).execute().data)
        except Exception: return True  # fail closed when distributed status is unknown
    return False
