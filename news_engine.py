"""Backward-compatible News Intelligence facade."""
from __future__ import annotations
import os, re
from core.http_client import http
from news_intelligence import enrich, clusters
from v8_store import connect, initialize, now_iso, save_snapshot

FEEDS=[x.strip() for x in os.getenv('NEWS_RSS_FEEDS','').split(',') if x.strip()]

def scan_news():
    initialize(); raw=[]
    for url in FEEDS:
        try:
            txt=http.get(url,timeout=(7,25),raise_for_status=False).text
            for item in re.findall(r'<item>(.*?)</item>',txt,re.S|re.I)[:40]:
                title=re.search(r'<title[^>]*>(.*?)</title>',item,re.S|re.I)
                raw.append({'title': title.group(1) if title else item, 'source':url})
        except Exception:
            continue
    out=[]
    for x in enrich(raw):
        with connect() as c:
            exists=c.execute('SELECT 1 FROM news_seen WHERE fingerprint=?',(x['fingerprint'],)).fetchone()
            if not exists:c.execute('INSERT INTO news_seen VALUES(?,?)',(x['fingerprint'],now_iso()))
        if not exists:
            out.append(x); save_snapshot('news',x,score=x['impact'])
    return out

def build_news_report():
    rows=scan_news()[:12]; lines=['<b>📰 NEWS INTELLIGENCE</b>','']
    if not FEEDS:return '\n'.join(lines+['Добавь RSS-источники через NEWS_RSS_FEEDS.','Fallback: сервис продолжает работу без остановки.'])
    if not rows:return '\n'.join(lines+['Новых материалов после удаления дублей нет.'])
    grouped=clusters(rows)
    lines += [f"Новых: <b>{len(rows)}</b> · Тем: <b>{len(grouped)}</b>",'']
    for x in rows:
        mood='+' if x['sentiment']>0 else ''
        lines += [f"<b>Impact {x['impact']} · {x['topic']}</b>",f"Sentiment: {mood}{x['sentiment']}",x['title'],'']
    return '\n'.join(lines)
