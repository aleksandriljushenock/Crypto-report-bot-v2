from __future__ import annotations
import json
from collections import Counter
import os


def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()


def _rows(table: str, limit: int = 20000):
    out=[]; start=0; page=1000
    while len(out)<limit:
        chunk=(_client().table(table).select('*').order('signal_created_at',desc=False)
               .range(start,min(start+page-1,limit-1)).execute().data or [])
        if not chunk: break
        out.extend(chunk)
        if len(chunk)<page: break
        start+=len(chunk)
    return out[:limit]


def main():
    rows=_rows('execution_training_dataset_v57')
    entry=Counter(str(r.get('entry_status') or 'missing') for r in rows)
    types=Counter(str(r.get('sample_type') or 'missing') for r in rows)
    outcomes=[r for r in rows if str(r.get('entry_status') or '').lower()=='filled' and r.get('net_return_pct') is not None]
    returns=[float(r.get('net_return_pct') or 0) for r in outcomes]
    gp=sum(x for x in returns if x>0); gl=abs(sum(x for x in returns if x<0))
    wins=sum(x>0 for x in returns)
    result={
        'rows':len(rows),'entry_status':dict(entry),'sample_types':dict(types),
        'resolved_execution':len(outcomes),'wins':wins,
        'win_rate_pct':round(wins/len(outcomes)*100,2) if outcomes else None,
        'avg_net_return_pct':round(sum(returns)/len(returns),4) if returns else None,
        'profit_factor':round(gp/gl,4) if gl else (99.0 if gp else None),
        'required':{
            'rows':int(os.getenv('EXECUTION_ML_MIN_SAMPLES','240')),
            'filled':int(os.getenv('EXECUTION_ML_MIN_FILLED_SAMPLES','80')),
            'no_fill':int(os.getenv('EXECUTION_ML_MIN_NO_FILL_SAMPLES','40')),
            'resolved_execution':int(os.getenv('EXECUTION_ML_MIN_RESOLVED_OUTCOMES','120')),
        },
    }
    req=result['required']
    result['ready_for_training']=(len(rows)>=req['rows'] and entry.get('filled',0)>=req['filled'] and entry.get('no_fill',0)>=req['no_fill'] and len(outcomes)>=req['resolved_execution'])
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
