import json
import os
from flask import Flask, jsonify, render_template_string
from ai_score_engine import get_top_scores, get_score_history, initialize_ai_store
from v8_store import latest, initialize
from portfolio_manager import get_positions

app = Flask(__name__)
HTML = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Crypto Intelligence v12</title>
<style>body{font-family:system-ui;background:#08101f;color:#eef;margin:0}.wrap{max-width:1400px;margin:auto;padding:24px}.hero{display:flex;justify-content:space-between;align-items:end;gap:20px}.muted{color:#94a3b8}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-top:20px}.card{background:#111a2e;border:1px solid #23304d;padding:18px;border-radius:16px;box-shadow:0 10px 30px #0003}.score{font-size:30px;font-weight:800}.bar{height:8px;background:#26324d;border-radius:10px;overflow:hidden}.bar i{display:block;height:100%;background:linear-gradient(90deg,#22c55e,#38bdf8);width:var(--w)}table{width:100%;border-collapse:collapse}td,th{padding:9px;border-bottom:1px solid #26324d;text-align:left}pre{white-space:pre-wrap;word-break:break-word;max-height:360px;overflow:auto}a{color:#7dd3fc}</style>
<div class=wrap><div class=hero><div><h1>Crypto Intelligence Platform v12</h1><div class=muted>Unified AI Score · Ranking · Adaptive learning · Market intelligence</div></div><div class=muted>Auto refresh: 60s</div></div>
<div class=grid>{% for row in top %}<div class=card><div class=muted>{{row.symbol}} · {{row.direction or 'N/A'}}</div><div class=score>{{'%.1f'|format(row.ai_score)}} / 100</div><div>{{row.tier}}</div><div class=bar><i style="--w:{{row.ai_score}}%"></i></div></div>{% endfor %}</div>
<div class=grid><div class=card><h2>🏆 TOP AI</h2><table><tr><th>Symbol</th><th>Score</th><th>Tier</th></tr>{% for r in top %}<tr><td>{{r.symbol}}</td><td>{{'%.1f'|format(r.ai_score)}}</td><td>{{r.tier}}</td></tr>{% endfor %}</table></div>
{% for title,data in cards %}<div class=card><h2>{{title}}</h2><pre>{{data}}</pre></div>{% endfor %}</div></div><script>setTimeout(()=>location.reload(),60000)</script></html>'''

@app.get('/')
def index():
    top = get_top_scores(12, 72)
    cards=[]
    for kind,title in [('capital_flow','Capital Flow'),('narrative','Narratives'),('smart_money','Smart Money'),('fear_greed','Fear & Greed')]:
        cards.append((title,json.dumps(latest(kind,8),ensure_ascii=False,indent=2,default=str)))
    cards.append(('Portfolio',json.dumps(get_positions(),ensure_ascii=False,indent=2,default=str)))
    return render_template_string(HTML, top=top, cards=cards)

@app.get('/api/<kind>')
def api(kind): return jsonify(latest(kind,100))

@app.get('/api/ai/top')
def ai_top(): return jsonify(get_top_scores(100,168))

@app.get('/api/ai/history/<symbol>')
def ai_history(symbol): return jsonify(get_score_history(symbol,100))

if __name__=='__main__':
    initialize(); initialize_ai_store()
    app.run(host=os.getenv('DASHBOARD_HOST','127.0.0.1'),port=int(os.getenv('DASHBOARD_PORT','8080')),debug=False)
