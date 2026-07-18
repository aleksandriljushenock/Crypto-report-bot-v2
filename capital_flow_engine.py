import math, os, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from v8_store import save_snapshot

BASE='https://fapi.binance.com'
S=requests.Session(); S.headers.update({'User-Agent':'crypto-report-service-v8/1.0'})

def _get(path, params=None):
    r=S.get(BASE+path,params=params,timeout=(7,25)); r.raise_for_status(); return r.json()

def _clamp(x): return max(0.0,min(100.0,float(x)))

def analyze_symbol(symbol):
    ticker=_get('/fapi/v1/ticker/24hr',{'symbol':symbol})
    premium=_get('/fapi/v1/premiumIndex',{'symbol':symbol})
    oi=_get('/fapi/v1/openInterest',{'symbol':symbol})
    klines=_get('/fapi/v1/klines',{'symbol':symbol,'interval':'15m','limit':48})
    taker=_get('/futures/data/takerlongshortRatio',{'symbol':symbol,'period':'15m','limit':12})
    closes=[float(k[4]) for k in klines]; vols=[float(k[5]) for k in klines]
    ret=(closes[-1]/closes[-9]-1)*100 if len(closes)>=9 and closes[-9] else 0
    vol_ratio=(sum(vols[-4:])/4)/(sum(vols[-20:-4])/16 or 1) if len(vols)>=20 else 1
    buy_sell=float(taker[-1].get('buySellRatio',1)) if taker else 1
    funding=float(premium.get('lastFundingRate') or 0)*100
    oi_usd=float(oi.get('openInterest') or 0)*float(ticker.get('lastPrice') or 0)
    long_score=_clamp(50 + ret*4 + (vol_ratio-1)*15 + (buy_sell-1)*25 - max(0,funding)*700)
    short_score=_clamp(50 - ret*4 + (vol_ratio-1)*15 + (1-buy_sell)*25 + min(0,funding)*-700)
    direction='LONG' if long_score>=short_score else 'SHORT'
    score=max(long_score,short_score)
    result={'symbol':symbol,'direction':direction,'score':round(score,1),'priceChange15m8':round(ret,3),'volumeRatio':round(vol_ratio,2),'takerBuySellRatio':round(buy_sell,3),'fundingPct':round(funding,5),'openInterestUsd':round(oi_usd,2),'liquidationHeatmap':'proxy: volatility+OI; external provider optional','cvdProxy':round((buy_sell-1)*100,2)}
    save_snapshot('capital_flow',result,symbol,score); return result

def scan_capital_flows(limit=20):
    rows=_get('/fapi/v1/ticker/24hr')
    symbols=[r['symbol'] for r in sorted(rows,key=lambda x:float(x.get('quoteVolume') or 0),reverse=True) if r['symbol'].endswith('USDT')][:max(5,int(limit))]
    out=[]
    with ThreadPoolExecutor(max_workers=min(6,len(symbols))) as ex:
        fut={ex.submit(analyze_symbol,s):s for s in symbols}
        for f in as_completed(fut):
            try:out.append(f.result())
            except Exception:continue
    return sorted(out,key=lambda x:x['score'],reverse=True)

def build_capital_flow_report(limit=10):
    items=scan_capital_flows(max(limit,20))[:limit]
    lines=['<b>💸 CAPITAL FLOW ENGINE</b>','']
    for i,x in enumerate(items,1):
        lines += [f"<b>{i}. {x['symbol']} — {x['direction']} {x['score']}</b>",f"OI ${x['openInterestUsd']:,.0f} | Funding {x['fundingPct']:.4f}%",f"Taker {x['takerBuySellRatio']:.2f} | Vol x{x['volumeRatio']:.2f} | CVD proxy {x['cvdProxy']:+.1f}",'']
    return '\n'.join(lines)
