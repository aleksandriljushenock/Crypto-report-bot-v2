"""V57 execution intelligence: unified Paper+Shadow dataset, purged OOS validation,
independent rolling ensembles, calibrated P(fill), P(profit|fill), and validated Expected-R.
"""
from __future__ import annotations
import os, math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List
from execution_features_v57 import FEATURE_NAMES, extract, specialist_key

MODEL_PATH=Path(os.getenv('EXECUTION_MODEL_V57_PATH','data/execution_model_v57.joblib'))
_CACHE={'mtime':None,'bundle':None}
META_ONLY={'score','raw_probability','final_probability','quality','ev'}
RAW_INDICES=[i for i,n in enumerate(FEATURE_NAMES) if n not in META_ONLY]
ALL_INDICES=list(range(len(FEATURE_NAMES)))

def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()

def _load_rows(limit:int=20000)->List[Dict[str,Any]]:
    out=[]; start=0; page=1000
    while len(out)<limit:
        rows=(_client().table('execution_training_dataset_v57').select('*').order('signal_created_at',desc=False).range(start,min(start+page-1,limit-1)).execute().data or [])
        if not rows:break
        out.extend(rows)
        if len(rows)<page:break
        start+=len(rows)
    return out[:limit]

def _dt(v):
    if not v:return None
    try:return datetime.fromisoformat(str(v).replace('Z','+00:00')).astimezone(timezone.utc)
    except Exception:return None

def _auc(y,p):
    try:
        from sklearn.metrics import roc_auc_score
        return float(roc_auc_score(y,p)) if len(set(y))>1 else None
    except Exception:return None

def _brier(y,p):return sum((float(a)-float(b))**2 for a,b in zip(p,y))/len(y) if y else None

def _mae(y,p):return sum(abs(float(a)-float(b)) for a,b in zip(y,p))/len(y) if y else None

def _precision_top(y,p,fraction=0.20):
    if not y:return None
    k=max(1,int(len(y)*fraction)); idx=sorted(range(len(p)),key=lambda i:p[i],reverse=True)[:k]
    return sum(int(y[i]) for i in idx)/len(idx) if idx else None

def _profit_factor(values):
    gp=sum(v for v in values if v>0); gl=abs(sum(v for v in values if v<0))
    return gp/gl if gl>1e-12 else (99.0 if gp>0 else 0.0)

def _spearman(a,b):
    if len(a)<3:return None
    try:
        from scipy.stats import spearmanr
        v=spearmanr(a,b).statistic
        return None if math.isnan(float(v)) else float(v)
    except Exception:return None

def _purged_split(rows, embargo_hours=None, min_segment_samples=None, return_meta=False):
    """55/15/15/15 chronological split with adaptive embargo.

    V57.1 fix: the old fixed 72h embargo could erase dense calibration/selection
    windows completely. We still purge temporal overlap, but progressively reduce
    the embargo until every split keeps enough observations. We never silently
    fall back to an unpurged split when timestamps are available.
    """
    if len(rows)<120:
        return (None, {'reason':'rows_lt_120','rows':len(rows)}) if return_meta else None
    min_seg=int(min_segment_samples if min_segment_samples is not None else os.getenv('EXECUTION_ML_MIN_SEGMENT_SAMPLES','20'))
    requested=float(embargo_hours if embargo_hours is not None else os.getenv('EXECUTION_ML_EMBARGO_HOURS','72'))
    floor=max(0.0,float(os.getenv('EXECUTION_ML_MIN_EMBARGO_HOURS','1')))
    decay=float(os.getenv('EXECUTION_ML_EMBARGO_DECAY','0.5'))
    if not 0 < decay < 1: decay=.5
    n=len(rows); i1=int(n*.55); i2=int(n*.70); i3=int(n*.85)
    raw=(rows[:i1],rows[i1:i2],rows[i2:i3],rows[i3:])
    bounds=[_dt(rows[i].get('signal_created_at')) for i in (i1,i2,i3)]
    if not all(bounds):
        meta={'reason':'timestamps_missing_unpurged','requested_embargo_hours':requested,'effective_embargo_hours':0.0,'sizes':[len(x) for x in raw]}
        return (raw,meta) if return_meta else raw
    b1,b2,b3=bounds
    def apply(emb):
        gap=timedelta(hours=emb)
        def before(seg,b): return [r for r in seg if (_dt(r.get('exit_at')) or _dt(r.get('signal_created_at')) or b) < b-gap]
        def between(seg,a,b): return [r for r in seg if (_dt(r.get('signal_created_at')) or a)>a+gap and (_dt(r.get('exit_at')) or _dt(r.get('signal_created_at')) or b)<b-gap]
        return (before(raw[0],b1),between(raw[1],b1,b2),between(raw[2],b2,b3),[r for r in raw[3] if (_dt(r.get('signal_created_at')) or b3)>b3+gap])
    tried=[]
    emb=max(requested,floor)
    while True:
        split=apply(emb); sizes=[len(x) for x in split]; tried.append((round(emb,6),sizes))
        if min(sizes)>=min_seg:
            meta={'reason':'ok','requested_embargo_hours':requested,'effective_embargo_hours':emb,'sizes':sizes,'attempts':tried}
            return (split,meta) if return_meta else split
        if emb<=floor+1e-9:break
        nxt=max(floor,emb*decay)
        if abs(nxt-emb)<1e-9:break
        emb=nxt
    meta={'reason':'segment_too_small_after_min_embargo','requested_embargo_hours':requested,'effective_embargo_hours':emb,'sizes':[len(x) for x in split],'attempts':tried,'min_segment_samples':min_seg}
    return (None,meta) if return_meta else None

def _weights(rows):
    return [max(.1,float(r.get('sample_weight') or 1.0)) for r in rows]

def _family(kind, seed):
    if kind=='hgb':
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(max_iter=int(os.getenv('EXECUTION_ML_MAX_ITER','500')),learning_rate=float(os.getenv('EXECUTION_ML_LEARNING_RATE','0.045')),max_leaf_nodes=int(os.getenv('EXECUTION_ML_MAX_LEAVES','21')),l2_regularization=float(os.getenv('EXECUTION_ML_L2','1.5')),random_state=seed)
    if kind=='extra':
        from sklearn.ensemble import ExtraTreesClassifier
        return ExtraTreesClassifier(n_estimators=int(os.getenv('EXECUTION_ML_TREES','350')),min_samples_leaf=int(os.getenv('EXECUTION_ML_MIN_LEAF','8')),max_features='sqrt',class_weight='balanced',n_jobs=int(os.getenv('EXECUTION_ML_N_JOBS','-1')),random_state=seed)
    from sklearn.ensemble import RandomForestClassifier
    return RandomForestClassifier(n_estimators=int(os.getenv('EXECUTION_ML_TREES','300')),min_samples_leaf=int(os.getenv('EXECUTION_ML_MIN_LEAF','8')),max_features='sqrt',class_weight='balanced_subsample',n_jobs=int(os.getenv('EXECUTION_ML_N_JOBS','-1')),random_state=seed)

def _regressor(seed):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=int(os.getenv('EXECUTION_ML_MAX_ITER','500')),learning_rate=float(os.getenv('EXECUTION_ML_LEARNING_RATE','0.045')),max_leaf_nodes=int(os.getenv('EXECUTION_ML_MAX_LEAVES','21')),l2_regularization=float(os.getenv('EXECUTION_ML_L2','1.5')),random_state=seed)

def _fit_one(rows,task,window,family='hgb',seed=55,feature_set='all'):
    from sklearn.isotonic import IsotonicRegression
    rows=rows[-window:] if len(rows)>window else list(rows)
    if task=='fill':
        usable=[r for r in rows if str(r.get('entry_status') or '').lower() in {'filled','no_fill','expired'}]
        label=lambda r:1 if str(r.get('entry_status')).lower()=='filled' else 0
    else:
        usable=[r for r in rows if str(r.get('entry_status') or '').lower()=='filled' and not bool(r.get('ambiguous_same_candle')) and str(r.get('outcome') or '').upper() not in {'','UNRESOLVED','AMBIGUOUS','OPEN'} and r.get('net_return_pct') is not None]
        label=lambda r:1 if float(r.get('net_return_pct') or 0)>0 else 0
    minimum=int(os.getenv('EXECUTION_ML_MIN_SAMPLES','240'))
    if len(usable)<minimum:
        return None, {'reason':'usable_lt_minimum','usable':len(usable),'minimum':minimum,'task':task,'window':window,'family':family,'feature_set':feature_set}
    split,split_meta=_purged_split(usable,return_meta=True)
    if not split:
        return None, {'reason':'purged_split_failed','task':task,'window':window,'family':family,'feature_set':feature_set,'usable':len(usable),'split':split_meta}
    tr,ca,se,ch=split
    min_seg=int(os.getenv('EXECUTION_ML_MIN_SEGMENT_SAMPLES','20'))
    if min(len(tr),len(ca),len(se),len(ch))<min_seg:
        return None, {'reason':'segment_lt_minimum','task':task,'window':window,'family':family,'feature_set':feature_set,'sizes':[len(tr),len(ca),len(se),len(ch)],'minimum':min_seg,'split':split_meta}
    ytr=[label(r) for r in tr]; yca=[label(r) for r in ca]; yse=[label(r) for r in se]; ych=[label(r) for r in ch]
    missing=[]
    for name,y in (('train',ytr),('selection',yse),('champion',ych)):
        if len(set(y))<2:missing.append(name)
    if missing:
        return None, {'reason':'single_class_segment','segments':missing,'task':task,'window':window,'family':family,'feature_set':feature_set,'sizes':[len(tr),len(ca),len(se),len(ch)],'split':split_meta}
    indices=RAW_INDICES if feature_set=='raw' else ALL_INDICES
    X=[[extract(r)[i] for i in indices] for r in tr]; Xc=[[extract(r)[i] for i in indices] for r in ca]; Xs=[[extract(r)[i] for i in indices] for r in se]; Xh=[[extract(r)[i] for i in indices] for r in ch]; sw=_weights(tr)
    clf=_family(family,seed)
    try:clf.fit(X,ytr,sample_weight=sw)
    except TypeError:clf.fit(X,ytr)
    cal=None
    if len(set(yca))>1:
        try:
            rawc=[float(x[1]) for x in clf.predict_proba(Xc)]; cal=IsotonicRegression(out_of_bounds='clip').fit(rawc,yca)
        except Exception:cal=None
    raw=[float(x[1]) for x in clf.predict_proba(Xs)]; pred=[float(x) for x in cal.predict(raw)] if cal is not None else raw
    auc=_auc(yse,pred); brier=_brier(yse,pred)
    rawh=[float(x[1]) for x in clf.predict_proba(Xh)]; predh=[float(x) for x in cal.predict(rawh)] if cal is not None else rawh
    champion_auc=_auc(ych,predh); champion_brier=_brier(ych,predh); champion_precision20=_precision_top(ych,predh)
    train_prior=(sum(ytr)+1.0)/(len(ytr)+2.0)
    base_rate_brier=_brier(yse,[train_prior]*len(yse)); champion_base_rate_brier=_brier(ych,[train_prior]*len(ych))
    if task=='outcome':
        baseline=[max(.001,min(.999,extract(r)[2]/100.0)) for r in se]; bauc=_auc(yse,baseline); bbrier=_brier(yse,baseline)
        baseh=[max(.001,min(.999,extract(r)[2]/100.0)) for r in ch]; champion_baseline_auc=_auc(ych,baseh); champion_baseline_brier=_brier(ych,baseh); champion_baseline_precision20=_precision_top(ych,baseh)
    else:
        baseline=[train_prior]*len(yse); bauc=.5; bbrier=base_rate_brier; baseh=[train_prior]*len(ych); champion_baseline_auc=.5; champion_baseline_brier=champion_base_rate_brier; champion_baseline_precision20=sum(ych)/len(ych)
    auc_floor=float(os.getenv('EXECUTION_ML_MIN_AUC','0.56')); gain=float(os.getenv('EXECUTION_ML_MIN_AUC_GAIN','0.02')); bmax=float(os.getenv('EXECUTION_ML_MAX_BRIER','0.26'))
    runtime_ok=bool(auc is not None and auc>=auc_floor and auc>=(bauc or .5)+gain and brier is not None and brier<=bmax and (bbrier is None or brier<bbrier) and (base_rate_brier is None or brier<base_rate_brier))
    champion_ok=bool(runtime_ok and champion_auc is not None and champion_auc>=float(os.getenv('EXECUTION_ML_CHAMPION_MIN_AUC','0.60')) and champion_auc>=(champion_baseline_auc or .5)+float(os.getenv('EXECUTION_ML_CHAMPION_MIN_AUC_GAIN','0.025')) and champion_brier is not None and champion_brier<=float(os.getenv('EXECUTION_ML_CHAMPION_MAX_BRIER','0.24')) and (champion_baseline_brier is None or champion_brier<champion_baseline_brier) and (champion_base_rate_brier is None or champion_brier<champion_base_rate_brier) and champion_precision20 is not None and champion_precision20 >= (champion_baseline_precision20 or 0)+float(os.getenv('EXECUTION_ML_CHAMPION_MIN_PRECISION20_GAIN','0.05')))
    reg=None; reg_metrics={}; return_ok=False
    if task=='outcome':
        target=[float(r.get('net_return_pct') or 0) for r in tr]; actual=[float(r.get('net_return_pct') or 0) for r in se]; actual_ch=[float(r.get('net_return_pct') or 0) for r in ch]
        reg=_regressor(seed+100); reg.fit(X,target,sample_weight=sw); rp=[float(v) for v in reg.predict(Xs)]; rph=[float(v) for v in reg.predict(Xh)]
        mae=_mae(actual,rp); baseline_value=sum(target)/len(target); base_mae=_mae(actual,[baseline_value]*len(actual)); rho=_spearman(rp,actual)
        sign=sum((a>0)==(p>0) for a,p in zip(actual,rp))/len(actual); ch_mae=_mae(actual_ch,rph); ch_rho=_spearman(rph,actual_ch); ch_sign=sum((a>0)==(p>0) for a,p in zip(actual_ch,rph))/len(actual_ch); ch_pf=_profit_factor([a for a,p in zip(actual_ch,rph) if p>0])
        return_ok=bool(mae is not None and base_mae is not None and mae<base_mae*float(os.getenv('EXECUTION_RETURN_MAX_MAE_RATIO','0.95')) and (rho or -1)>=float(os.getenv('EXECUTION_RETURN_MIN_SPEARMAN','0.12')) and sign>=float(os.getenv('EXECUTION_RETURN_MIN_SIGN_ACCURACY','0.56')) and (ch_rho or -1)>=float(os.getenv('EXECUTION_RETURN_CHAMPION_MIN_SPEARMAN','0.12')) and ch_sign>=float(os.getenv('EXECUTION_RETURN_CHAMPION_MIN_SIGN_ACCURACY','0.56')) and ch_pf>=float(os.getenv('EXECUTION_RETURN_CHAMPION_MIN_PF','1.10')))
        reg_metrics={'return_mae':mae,'baseline_return_mae':base_mae,'return_spearman':rho,'return_sign_accuracy':sign,'champion_return_mae':ch_mae,'champion_return_spearman':ch_rho,'champion_return_sign_accuracy':ch_sign,'champion_return_pf':ch_pf,'return_runtime_ok':return_ok}
        champion_ok=bool(champion_ok and return_ok)
    return {'classifier':clf,'calibrator':cal,'regressor':reg,'window':window,'family':family,'feature_set':feature_set,'feature_indices':indices,'samples':len(usable),'train_samples':len(tr),'calibration_samples':len(ca),'selection_samples':len(se),'champion_samples':len(ch),'split_meta':split_meta,'auc':auc,'brier':brier,'baseline_auc':bauc,'baseline_brier':bbrier,'base_rate_brier':base_rate_brier,'champion_base_rate_brier':champion_base_rate_brier,'runtime_ok':runtime_ok,'champion_ok':champion_ok,'champion_auc':champion_auc,'champion_brier':champion_brier,'champion_precision20':champion_precision20,'champion_baseline_auc':champion_baseline_auc,'champion_baseline_brier':champion_baseline_brier,'champion_baseline_precision20':champion_baseline_precision20,**reg_metrics}, None

def _train_group(rows,key):
    windows=[int(x) for x in os.getenv('EXECUTION_ML_WINDOWS','500,1000,2500,5000').split(',') if x.strip()]
    families=[x.strip() for x in os.getenv('EXECUTION_ML_FAMILIES','hgb,extra,rf').split(',') if x.strip()]
    fill=[]; outcome=[]; rejections=[]
    feature_sets=[x.strip() for x in os.getenv('EXECUTION_ML_FEATURE_SETS','raw,all').split(',') if x.strip()]
    for wi,w in enumerate(windows):
        for fi,fam in enumerate(families):
            for si,feature_set in enumerate(feature_sets):
                seed=55+wi*17+fi*101+si*313
                m,reason=_fit_one(rows,'fill',w,fam,seed,feature_set)
                if m:fill.append(m)
                elif reason:rejections.append(reason)
                m,reason=_fit_one(rows,'outcome',w,fam,seed+7,feature_set)
                if m:outcome.append(m)
                elif reason:rejections.append(reason)
    fill_labels=[1 if str(r.get('entry_status') or '').lower()=='filled' else 0 for r in rows if str(r.get('entry_status') or '').lower() in {'filled','no_fill','expired'}]
    fill_prior=(sum(fill_labels)+2)/(len(fill_labels)+4) if fill_labels else None
    return {'fill':fill,'outcome':outcome,'rejections':rejections,'samples':len(rows),'key':key,'fill_prior':fill_prior}

def _cloud_path():return os.getenv('EXECUTION_MODEL_V57_CLOUD_PATH','v57/latest/execution_model_v57.joblib')
def _upload(path):
    try:
        st=_client().storage.from_(os.getenv('SUPABASE_MODEL_BUCKET','models')); data=path.read_bytes(); obj=_cloud_path()
        try:st.upload(obj,data,{'content-type':'application/octet-stream','upsert':'true'})
        except Exception:st.update(obj,data,{'content-type':'application/octet-stream'})
        return True
    except Exception:return False

def _restore():
    try:
        data=_client().storage.from_(os.getenv('SUPABASE_MODEL_BUCKET','models')).download(_cloud_path()); MODEL_PATH.parent.mkdir(parents=True,exist_ok=True); MODEL_PATH.write_bytes(data); return True
    except Exception:return False

def train(trigger='manual'):
    import joblib
    rows=_load_rows(int(os.getenv('EXECUTION_ML_MAX_ROWS','20000')))
    minimum=int(os.getenv('EXECUTION_ML_MIN_SAMPLES','240'))
    if len(rows)<minimum:return {'status':'insufficient-data','rows':len(rows)}
    entry_labels=[str(r.get('entry_status') or '').lower() for r in rows]
    filled=sum(x=='filled' for x in entry_labels); no_fill=sum(x in {'no_fill','expired'} for x in entry_labels)
    resolved_outcome=sum(1 for r in rows if str(r.get('entry_status') or '').lower()=='filled' and not bool(r.get('ambiguous_same_candle')) and str(r.get('outcome') or '').upper() not in {'','UNRESOLVED','AMBIGUOUS','OPEN'} and r.get('net_return_pct') is not None)
    label_quality={'filled':filled,'no_fill':no_fill,'resolved_outcome':resolved_outcome,'rows':len(rows)}
    min_filled=int(os.getenv('EXECUTION_ML_MIN_FILLED_SAMPLES','80')); min_no_fill=int(os.getenv('EXECUTION_ML_MIN_NO_FILL_SAMPLES','40')); min_outcomes=int(os.getenv('EXECUTION_ML_MIN_RESOLVED_OUTCOMES','120'))
    if filled<min_filled or no_fill<min_no_fill or resolved_outcome<min_outcomes:
        return {'status':'invalid-label-balance','rows':len(rows),'label_quality':label_quality,'required':{'filled':min_filled,'no_fill':min_no_fill,'resolved_outcome':min_outcomes}}
    groups={'GLOBAL':rows}; min_spec=int(os.getenv('EXECUTION_ML_SPECIALIST_MIN_SAMPLES','360'))
    for key in sorted({specialist_key(r) for r in rows}):
        subset=[r for r in rows if specialist_key(r)==key]
        if len(subset)>=min_spec:groups[key]=subset
    models={k:_train_group(v,k) for k,v in groups.items()}
    healthy=[m for g in models.values() for m in g['outcome'] if m.get('runtime_ok')]
    champions=[m for g in models.values() for m in g['outcome'] if m.get('champion_ok')]
    min_champ=max(1,int(os.getenv('EXECUTION_ML_CHAMPION_MIN_MODELS','2')))
    status='champion' if len(champions)>=min_champ else 'shadow'
    bundle={'schema':57,'version':'execution-ensemble-v57.1-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'),'status':status,'trained_at':datetime.now(timezone.utc).isoformat(),'feature_names':FEATURE_NAMES,'models':models,'rows':len(rows),'trigger':trigger}
    MODEL_PATH.parent.mkdir(parents=True,exist_ok=True); joblib.dump(bundle,MODEL_PATH); cloud=_upload(MODEL_PATH); invalidate_cache()
    trained_models=sum(len(g.get('fill') or [])+len(g.get('outcome') or []) for g in models.values())
    rejection_counts={}
    for g in models.values():
        for r in g.get('rejections') or []:
            k=r.get('reason','unknown'); rejection_counts[k]=rejection_counts.get(k,0)+1
    return {'status':status,'version':bundle['version'],'rows':len(rows),'groups':list(models),'trained_models':trained_models,'healthy_models':len(healthy),'champion_models':len(champions),'best_auc':max((m.get('champion_auc') or 0 for m in champions),default=None),'rejection_counts':rejection_counts,'cloud_saved':cloud,'label_quality':label_quality}

def invalidate_cache():_CACHE.update(mtime=None,bundle=None)
def _load_bundle():
    if not MODEL_PATH.exists() and not _restore():return None
    mt=MODEL_PATH.stat().st_mtime
    if _CACHE.get('mtime')==mt:return _CACHE.get('bundle')
    try:
        import joblib; b=joblib.load(MODEL_PATH); _CACHE.update(mtime=mt,bundle=b); return b
    except Exception:return None

def _ensemble(models,x, require_champion=False):
    ps=[]; rs=[]; weights=[]; aucs=[]
    for m in models:
        if not m.get('runtime_ok'):continue
        if require_champion and not m.get('champion_ok'):continue
        try:
            indices=m.get('feature_indices') or ALL_INDICES; xx=[x[i] for i in indices]
            p=float(m['classifier'].predict_proba([xx])[0][1]); cal=m.get('calibrator'); p=float(cal.predict([p])[0]) if cal is not None else p
            auc=float(m.get('champion_auc') or m.get('auc') or .5); brier=float(m.get('champion_brier') or m.get('brier') or .25)
            w=max(.01,(auc-.50)**2/max(.05,brier)); ps.append(p); weights.append(w); aucs.append(auc)
            if m.get('regressor') is not None and m.get('return_runtime_ok'):rs.append((float(m['regressor'].predict([xx])[0]),w))
        except Exception:continue
    if not ps:return None
    import statistics
    total=sum(weights); prob=sum(p*w for p,w in zip(ps,weights))/total
    ret=sum(v*w for v,w in rs)/sum(w for _,w in rs) if rs else None
    return {'probability':prob*100,'uncertainty':statistics.pstdev(ps)*100 if len(ps)>1 else 15.0,'expected_return_pct':ret,'models':len(ps),'mean_auc':sum(a*w for a,w in zip(aucs,weights))/total if total else None}

def predict(signal:Dict[str,Any])->Dict[str,Any]:
    b=_load_bundle()
    if not b or b.get('status')!='champion':return {'available':False,'status':(b or {}).get('status','missing')}
    key=specialist_key(signal); models=b.get('models') or {}; g=models.get(key) or models.get('GLOBAL')
    if not g:return {'available':False,'status':'no-group'}
    x=extract(signal); outcome=_ensemble(g.get('outcome') or [],x,require_champion=True); fill=_ensemble(g.get('fill') or [],x,require_champion=False)
    if not outcome and g.get('key')!='GLOBAL':
        g=models.get('GLOBAL') or {}; outcome=_ensemble(g.get('outcome') or [],x,require_champion=True); fill=_ensemble(g.get('fill') or [],x,require_champion=False)
    if not outcome:return {'available':False,'status':'no-validated-outcome-model'}
    # Never assume 100% fill. Use specialist/global empirical Bayesian prior if no validated fill model.
    fill_prob=(fill or {}).get('probability')
    fill_source='model'
    if fill_prob is None:
        prior=g.get('fill_prior'); prior=prior if prior is not None else models.get('GLOBAL',{}).get('fill_prior')
        if prior is None:return {'available':False,'status':'no-fill-evidence'}
        fill_prob=float(prior)*100; fill_source='empirical_prior'
    return {'available':True,'version':b.get('version'),'group':g.get('key'),'fillProbability':round(fill_prob,2),'fillProbabilitySource':fill_source,'profitProbability':round(outcome['probability'],2),'expectedReturnPct':None if outcome.get('expected_return_pct') is None else round(outcome['expected_return_pct'],4),'uncertainty':round(outcome.get('uncertainty',15),2),'modelCount':outcome.get('models'),'meanAuc':outcome.get('mean_auc')}
