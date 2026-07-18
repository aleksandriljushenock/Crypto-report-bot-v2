import os, re, requests
from collections import Counter
from v8_store import save_snapshot

NARRATIVES={'AI':[' ai ','artificial intelligence','agent','gpu'],'RWA':['rwa','real world asset','tokenization'],'DePIN':['depin','physical infrastructure'],'Gaming':['gaming','gamefi'],'Memecoin':['meme','memecoin'],'L2':['layer 2','rollup','l2'],'Stablecoin':['stablecoin'],'BTCFi':['btcfi','bitcoin defi'],'DeFi':['defi','dex','yield']}
FEEDS=[x.strip() for x in os.getenv('NEWS_RSS_FEEDS','').split(',') if x.strip()]

def scan_narratives():
    corpus=''
    for url in FEEDS:
        try: corpus+=' '+requests.get(url,timeout=(7,25),headers={'User-Agent':'crypto-report-v8'}).text.lower()
        except Exception: pass
    counts=Counter()
    for name,terms in NARRATIVES.items(): counts[name]=sum(corpus.count(t) for t in terms)
    maximum=max(counts.values() or [1]) or 1
    rows=[{'narrative':k,'mentions':v,'score':round(35+65*v/maximum,1) if v else 20.0} for k,v in counts.most_common()]
    for x in rows: save_snapshot('narrative',x,x['narrative'],x['score'])
    return rows

def build_narrative_report():
    rows=scan_narratives(); lines=['<b>🧠 NARRATIVE AI</b>','']
    if not FEEDS: lines += ['NEWS_RSS_FEEDS не настроен — показан нейтральный профиль.','']
    for i,x in enumerate(rows,1): lines.append(f"{i}. <b>{x['narrative']}</b>: {x['score']} ({x['mentions']} упоминаний)")
    return '\n'.join(lines)
