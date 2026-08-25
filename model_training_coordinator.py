"""V46 training coordinator: thread + host flock + optional Supabase distributed lease."""
from __future__ import annotations
import os, socket, threading, uuid
from contextlib import contextmanager
from pathlib import Path
try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl=None
_LOCAL=threading.Lock()
_LOCK_PATH=Path(os.getenv('MODEL_TRAINING_LOCK_FILE','data/model_training.lock'))

def _distributed_enabled():
    return bool(os.getenv('SUPABASE_URL')) and bool(os.getenv('SUPABASE_SERVICE_KEY')) and os.getenv('MODEL_TRAINING_DISTRIBUTED_LOCK','true').lower() not in {'0','false','no','off'}

def _acquire_distributed(token):
    if not _distributed_enabled(): return True
    try:
        from cloud_client import get_supabase_client
        data=get_supabase_client().rpc('model_training_lease_acquire_v46',{'p_lock_name':'v14-training','p_token':token,'p_owner':socket.gethostname(),'p_ttl_seconds':max(300,int(os.getenv('MODEL_TRAINING_LEASE_SECONDS','21600')))}).execute().data
        return bool(data)
    except Exception:
        # With cloud configured, failure to prove exclusivity must be fail-closed.
        return False

def _release_distributed(token):
    if not _distributed_enabled(): return
    try:
        from cloud_client import get_supabase_client
        get_supabase_client().rpc('model_training_lease_release_v46',{'p_lock_name':'v14-training','p_token':token}).execute()
    except Exception:
        pass

@contextmanager
def training_slot():
    if not _LOCAL.acquire(blocking=False):
        yield False; return
    fh=None; file_locked=False; token=uuid.uuid4().hex; distributed=False
    try:
        if fcntl is not None:
            _LOCK_PATH.parent.mkdir(parents=True,exist_ok=True); fh=open(_LOCK_PATH,'a+')
            try: fcntl.flock(fh.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB); file_locked=True
            except BlockingIOError: yield False; return
        else: file_locked=True
        distributed=_acquire_distributed(token)
        if not distributed: yield False; return
        yield True
    finally:
        if distributed: _release_distributed(token)
        if file_locked and fh is not None and fcntl is not None:
            try: fcntl.flock(fh.fileno(),fcntl.LOCK_UN)
            except Exception: pass
        if fh is not None:
            try: fh.close()
            except Exception: pass
        _LOCAL.release()

def training_running()->bool:
    if not _LOCAL.acquire(blocking=False): return True
    _LOCAL.release()
    if fcntl is None: return False
    _LOCK_PATH.parent.mkdir(parents=True,exist_ok=True)
    with open(_LOCK_PATH,'a+') as fh:
        try:
            fcntl.flock(fh.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB); fcntl.flock(fh.fileno(),fcntl.LOCK_UN); return False
        except BlockingIOError: return True
