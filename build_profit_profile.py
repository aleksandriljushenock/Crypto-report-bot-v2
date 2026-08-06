import json, math
from pathlib import Path
import pandas as pd

SRC=Path('/mnt/data/Supabase Snippet Untitled query (1).csv')
OUT=Path('/mnt/data/ai_hedge_upgrade/data/profit_profile_v2.json')
OUT.parent.mkdir(parents=True, exist_ok=True)
df=pd.read_csv(SRC)

def js(x):
    if not isinstance(x,str) or not x: return {}
    try:return json.loads(x)
    except:return {}
rows=[]
for _,r in df.iterrows():
    f=js(r.get('features')); rr=js(r.get('real_result'))
    ret=float(rr.get('return_percent') or r.get('price_change_pct') or 0)
    factors=f.get('aiFactors') or {}
    rows.append({
      'symbol':r.get('symbol'),'direction':r.get('signal_direction'),'return':ret,
      'win':1 if bool(rr.get('success')) else 0,'setup':f.get('setup','NONE'),
      'regime':f.get('marketRegime') or f.get('aiRegime') or 'unknown',
      'structure1h':f.get('structure1h','N/A'),'structure15m':f.get('structure15m','N/A'),
      'score':float(r.get('signal_score') or 0),'probability':float(f.get('probability') or r.get('signal_confidence') or 0),
      'confidence':float(f.get('confidence') or 0),'uncertainty':float(f.get('uncertainty') or 100),
      'quoteVolume':float(f.get('quoteVolume') or 0),'rr':float(f.get('rr') or 0),
      'tf1d':(f.get('timeframes') or {}).get('1d','N/A'),'tf4h':(f.get('timeframes') or {}).get('4h','N/A'),
      'tf1h':(f.get('timeframes') or {}).get('1h','N/A'),'tf15m':(f.get('timeframes') or {}).get('15m','N/A'),
      'tf5m':(f.get('timeframes') or {}).get('5m','N/A'),
      **{k:float(factors.get(k) or 50) for k in ['trend','momentum','volume','funding','open_interest','alignment','risk_reward','capital_flow','smart_money','news','narrative']}
    })
x=pd.DataFrame(rows)

def stats(g):
    n=len(g); wins=int(g.win.sum()); losses=n-wins
    aw=float(g.loc[g['return']>0,'return'].mean()) if (g['return']>0).any() else 0
    al=abs(float(g.loc[g['return']<=0,'return'].mean())) if (g['return']<=0).any() else 0
    return {'samples':n,'win_rate':round(wins/n*100,2),'avg_return':round(float(g['return'].mean()),4),
            'avg_win':round(aw,4),'avg_loss':round(al,4),'profit_factor':round(float(g.loc[g['return']>0,'return'].sum()/abs(g.loc[g['return']<0,'return'].sum())) if (g['return']<0).any() else 99,4)}
profile={'version':'profit-profile-v2-1144','overall':stats(x),'groups':{},'rules':[]}
for col in ['setup','regime','structure1h','structure15m','tf1d','tf4h','tf1h','tf15m','tf5m','symbol']:
    profile['groups'][col]={str(k):stats(g) for k,g in x.groupby(col) if len(g)>=8}

# Validated thresholds and interactions from broad sample.
rules=[
 ('boost','PULLBACK',lambda d:d.setup.eq('PULLBACK'),5),
 ('penalty','BREAKOUT',lambda d:d.setup.eq('BREAKOUT'),-5),
 ('boost','flow_alignment_volume',lambda d:(d.capital_flow>=62)&(d.alignment>=75)&(d.volume>=65),9),
 ('boost','smart_pullback_volume',lambda d:(d.smart_money>=60)&d.setup.eq('PULLBACK')&(d.volume>=65),7),
 ('boost','smart_pullback_probability',lambda d:(d.smart_money>=60)&d.setup.eq('PULLBACK')&(d.probability>=70),6),
 ('boost','probability_trend_liquidity',lambda d:(d.probability>=72)&(d.trend>=85)&(d.quoteVolume>=130_000_000),6),
 ('boost','daily_micro_alignment',lambda d:d.tf1d.eq('UP')&d.tf5m.eq('UP')&(d.alignment>=75),5),
 ('boost','structure_1h_long',lambda d:d.structure1h.isin(['SWEEP_HIGH','BOS_UP']),4),
 ('penalty','low_liquidity',lambda d:d.quoteVolume<130_000_000,-8),
 ('penalty','weak_capital_flow',lambda d:d.capital_flow<=50,-8),
 ('penalty','low_confidence',lambda d:d.confidence<36,-7),
 ('penalty','high_uncertainty',lambda d:d.uncertainty>64,-7),
 ('penalty','long_against_4h',lambda d:d.tf4h.eq('DOWN'),-12),
 ('penalty','weak_breakout',lambda d:d.setup.eq('BREAKOUT')&(d.volume<60),-8),
 ('penalty','breakout_micro_weak',lambda d:d.setup.eq('BREAKOUT')&d.tf5m.isin(['DOWN','RANGE']),-6),
]
for kind,name,fn,adj in rules:
    g=x[fn(x)]
    if len(g)>=8:
        s=stats(g); s.update({'kind':kind,'name':name,'adjustment':adj})
        profile['rules'].append(s)
OUT.write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
print(OUT, len(x), profile['overall'])
