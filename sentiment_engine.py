import requests
from v8_store import save_snapshot

def get_fear_greed():
    try:
        d=requests.get('https://api.alternative.me/fng/',params={'limit':1},timeout=(7,20)).json()['data'][0]
        value=int(d['value']); result={'value':value,'classification':d['value_classification'],'bias':'contrarian-long' if value<25 else 'risk-off' if value>75 else 'neutral'}
    except Exception as e: result={'value':50,'classification':'Unavailable','bias':'neutral','error':str(e)}
    save_snapshot('fear_greed',result,score=result['value']); return result

def build_sentiment_report():
    x=get_fear_greed(); return f"<b>😨 FEAR & GREED</b>\n\nIndex: <b>{x['value']}</b> — {x['classification']}\nРежим: <b>{x['bias']}</b>"
