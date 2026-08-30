"""V54 rolling execution intelligence model.

Two-stage prediction:
  1. P(entry fill)
  2. P(profit | fill) + expected execution return
Models are chronological rolling HistGradientBoosting ensembles. They are fail-closed:
only models that beat configurable holdout floors become runtime champions.
"""
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from execution_features_v54 import FEATURE_NAMES, extract, specialist_key

MODEL_PATH=Path(os.getenv('EXECUTION_MODEL_V54_PATH','data/execution_model_v54.joblib'))
_CACHE={'mtime':None,'bundle':None}

def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()

def _load_rows(limit:int=10000)->List[Dict[str,Any]]:
    out=[]; start=0; page=1000
    while len(out)<limit:
        rows=(_client().table('execution_training_dataset_v54').select('*').order('signal_created_at',desc=False).range(start,min(start+page-1,limit-1)).execute().data or [])
        if not rows:break
        out.extend(rows)
        if len(rows)<page:break
        start+=len(rows)
    return out[:limit]

def _auc(y,p):
    try:
        from sklearn.metrics import roc_auc_score
        return float(roc_auc_score(y,p)) if len(set(y))>1 else None
    except Exception:return None

def _brier(y,p):
    if not y:return None
    return sum((float(a)-float(b))**2 for a,b in zip(p,y))/len(y)

def _fit_one(rows, task:str, window:int):
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.isotonic import IsotonicRegression
    rows=rows[-window:] if len(rows)>window else list(rows)
    if task=='fill':
        usable=[r for r in rows if str(r.get('entry_status') or '').lower() in {'filled','expired','no_fill'}]
        y=[1 if str(r.get('entry_status')).lower()=='filled' else 0 for r in usable]
    else:
        usable=[r for r in rows if str(r.get('entry_status') or '').lower()=='filled' and str(r.get('outcome') or '').upper() not in {'','UNRESOLVED','OPEN'} and r.get('net_return_pct') is not None]
        y=[1 if float(r.get('net_return_pct') or 0)>0 else 0 for r in usable]
    minimum=int(os.getenv('EXECUTION_ML_MIN_SAMPLES','120'))
    if len(usable)<minimum or len(set(y))<2:return None
    # Strict chronological 65/17/18 split: calibration data is never reused as test data.
    n=len(usable); train_end=max(40,int(n*0.65)); cal_end=max(train_end+20,int(n*0.82)); cal_end=min(cal_end,n-20)
    if cal_end<=train_end or n-cal_end<20:return None
    tr,ca,te=usable[:train_end],usable[train_end:cal_end],usable[cal_end:]
    ytr=y[:train_end]; yca=y[train_end:cal_end]; yte=y[cal_end:]
    if len(set(ytr))<2 or len(set(yte))<2:return None
    X=[extract(r) for r in tr]; Xc=[extract(r) for r in ca]; Xt=[extract(r) for r in te]
    clf=HistGradientBoostingClassifier(max_iter=int(os.getenv('EXECUTION_ML_MAX_ITER','300')),learning_rate=float(os.getenv('EXECUTION_ML_LEARNING_RATE','0.055')),max_leaf_nodes=int(os.getenv('EXECUTION_ML_MAX_LEAVES','15')),l2_regularization=float(os.getenv('EXECUTION_ML_L2','1.0')),random_state=54)
    clf.fit(X,ytr)
    calibrator=None
    if len(ca)>=20 and len(set(yca))>1:
        try:
            raw_cal=[float(x[1]) for x in clf.predict_proba(Xc)]
            calibrator=IsotonicRegression(out_of_bounds='clip').fit(raw_cal,yca)
        except Exception:calibrator=None
    raw=[float(x[1]) for x in clf.predict_proba(Xt)]; calibrated=[float(x) for x in calibrator.predict(raw)] if calibrator is not None else raw
    auc=_auc(yte,calibrated); brier=_brier(yte,calibrated)
    # Existing signal Probability is the real promotion benchmark for outcome models.
    if task=='outcome':
        baseline=[max(.001,min(.999,extract(r)[1]/100.0)) for r in te]
        baseline_auc=_auc(yte,baseline); baseline_brier=_brier(yte,baseline)
    else:
        base=sum(ytr)/len(ytr); baseline=[base]*len(yte); baseline_auc=0.5; baseline_brier=_brier(yte,baseline)
    reg=None; reg_mae=None
    if task=='outcome':
        target=[float(r.get('net_return_pct') or 0) for r in tr]
        reg=HistGradientBoostingRegressor(max_iter=int(os.getenv('EXECUTION_ML_MAX_ITER','300')),learning_rate=float(os.getenv('EXECUTION_ML_LEARNING_RATE','0.055')),max_leaf_nodes=int(os.getenv('EXECUTION_ML_MAX_LEAVES','15')),l2_regularization=float(os.getenv('EXECUTION_ML_L2','1.0')),random_state=55).fit(X,target)
        pred=reg.predict(Xt); actual=[float(r.get('net_return_pct') or 0) for r in te]; reg_mae=sum(abs(float(a)-float(b)) for a,b in zip(pred,actual))/len(actual)
    auc_floor=float(os.getenv('EXECUTION_ML_MIN_AUC','0.54')); min_gain=float(os.getenv('EXECUTION_ML_MIN_AUC_GAIN','0.02')); brier_max=float(os.getenv('EXECUTION_ML_MAX_BRIER','0.30'))
    runtime_ok=(auc is not None and auc>=auc_floor and (baseline_auc is None or auc>=baseline_auc+min_gain) and brier is not None and brier<=brier_max and (baseline_brier is None or brier<baseline_brier))
    return {'classifier':clf,'calibrator':calibrator,'regressor':reg,'window':window,'samples':len(usable),'validation':len(te),'calibration_samples':len(ca),'auc':auc,'brier':brier,'baseline_auc':baseline_auc,'baseline_brier':baseline_brier,'return_mae':reg_mae,'runtime_ok':bool(runtime_ok)}

def _train_group(rows,key):
    windows=[int(x) for x in os.getenv('EXECUTION_ML_WINDOWS','250,500,1000').split(',') if x.strip()]
    fill=[m for w in windows if (m:=_fit_one(rows,'fill',w))]
    outcome=[m for w in windows if (m:=_fit_one(rows,'outcome',w))]
    return {'fill':fill,'outcome':outcome,'samples':len(rows),'key':key}

def _storage():
    from cloud_client import get_supabase_client
    return get_supabase_client().storage.from_(os.getenv('SUPABASE_MODEL_BUCKET','models'))

def _cloud_object_path(): return os.getenv('EXECUTION_MODEL_V54_CLOUD_PATH','v54/latest/execution_model_v54.joblib')

def _upload_cloud(path:Path):
    try:
        data=path.read_bytes(); st=_storage(); obj=_cloud_object_path(); opts={'content-type':'application/octet-stream','upsert':'true'}
        try: st.upload(obj,data,opts)
        except Exception:
            try: st.update(obj,data,{'content-type':'application/octet-stream'})
            except Exception: st.remove([obj]); st.upload(obj,data,{'content-type':'application/octet-stream'})
        return True
    except Exception:return False

def _restore_cloud():
    if MODEL_PATH.exists(): return True
    try:
        data=_storage().download(_cloud_object_path()); data=data if isinstance(data,bytes) else bytes(getattr(data,'content',data)); MODEL_PATH.parent.mkdir(parents=True,exist_ok=True); MODEL_PATH.write_bytes(data); return True
    except Exception:return False

def train(trigger='scheduled')->Dict[str,Any]:
    try:
        import sklearn, joblib
    except Exception as exc:return {'status':'dependency-missing','error':str(exc)}
    rows=_load_rows(int(os.getenv('EXECUTION_ML_MAX_ROWS','10000')))
    if not rows:return {'status':'no-data'}
    groups={'GLOBAL':rows}
    min_spec=int(os.getenv('EXECUTION_ML_SPECIALIST_MIN_SAMPLES','180'))
    for key in sorted({specialist_key(r) for r in rows}):
        subset=[r for r in rows if specialist_key(r)==key]
        if len(subset)>=min_spec:groups[key]=subset
    models={k:_train_group(v,k) for k,v in groups.items()}
    # Promotion requires at least one healthy outcome model; otherwise persist shadow only.
    auc_floor=float(os.getenv('EXECUTION_ML_MIN_AUC','0.54')); brier_max=float(os.getenv('EXECUTION_ML_MAX_BRIER','0.30'))
    candidates=[m for g in models.values() for m in g['outcome'] if m.get('auc') is not None]
    healthy=[m for m in candidates if m.get('runtime_ok')]
    status='champion' if healthy else 'shadow'
    bundle={'schema':54,'version':'execution-hgb-v54-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'),'status':status,'trained_at':datetime.now(timezone.utc).isoformat(),'feature_names':FEATURE_NAMES,'models':models,'rows':len(rows),'trigger':trigger}
    MODEL_PATH.parent.mkdir(parents=True,exist_ok=True); joblib.dump(bundle,MODEL_PATH)
    cloud_saved=_upload_cloud(MODEL_PATH)
    invalidate_cache()
    return {'status':status,'version':bundle['version'],'rows':len(rows),'groups':list(models),'healthy_models':len(healthy),'best_auc':max((m['auc'] for m in candidates if m.get('auc') is not None),default=None),'cloud_saved':cloud_saved}

def invalidate_cache():_CACHE.update(mtime=None,bundle=None)

def _load_bundle():
    if not MODEL_PATH.exists() and not _restore_cloud():return None
    mt=MODEL_PATH.stat().st_mtime
    if _CACHE.get('mtime')==mt:return _CACHE.get('bundle')
    try:
        import joblib; b=joblib.load(MODEL_PATH); _CACHE.update(mtime=mt,bundle=b); return b
    except Exception:return None

def _ensemble(models,x):
    ps=[]; rs=[]; aucs=[]
    for m in models:
        if not m.get('runtime_ok'): continue
        try:
            p=float(m['classifier'].predict_proba([x])[0][1]); cal=m.get('calibrator'); p=float(cal.predict([p])[0]) if cal is not None else p; ps.append(p); aucs.append(float(m.get('auc') or .5))
            if m.get('regressor') is not None:rs.append(float(m['regressor'].predict([x])[0]))
        except Exception:continue
    if not ps:return None
    import statistics
    return {'probability':sum(ps)/len(ps)*100,'uncertainty':(statistics.pstdev(ps)*100 if len(ps)>1 else 12.0),'expected_return_pct':sum(rs)/len(rs) if rs else None,'models':len(ps),'mean_auc':sum(aucs)/len(aucs) if aucs else None}

def predict(signal:Dict[str,Any])->Dict[str,Any]:
    b=_load_bundle()
    if not b or b.get('status')!='champion':return {'available':False,'status':(b or {}).get('status','missing')}
    key=specialist_key(signal); g=(b.get('models') or {}).get(key) or (b.get('models') or {}).get('GLOBAL')
    if not g:return {'available':False,'status':'no-group'}
    x=extract(signal); fill=_ensemble(g.get('fill') or [],x); outcome=_ensemble(g.get('outcome') or [],x)
    if not outcome and g.get('key')!='GLOBAL':
        g=(b.get('models') or {}).get('GLOBAL') or {}; fill=_ensemble(g.get('fill') or [],x); outcome=_ensemble(g.get('outcome') or [],x)
    if not outcome:return {'available':False,'status':'no-validated-outcome-model'}
    return {'available':True,'version':b.get('version'),'group':g.get('key'),'fillProbability':round((fill or {}).get('probability',100),2),'profitProbability':round(outcome['probability'],2),'expectedReturnPct':None if outcome.get('expected_return_pct') is None else round(outcome['expected_return_pct'],4),'uncertainty':round(outcome.get('uncertainty',12),2),'modelCount':outcome.get('models'),'meanAuc':outcome.get('mean_auc')}
