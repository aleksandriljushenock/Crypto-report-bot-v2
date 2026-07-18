import os, requests
from v8_store import connect, initialize, now_iso

def set_position(symbol, quantity, avg_price=0):
    initialize(); symbol=symbol.upper().replace('/','')
    with connect() as c: c.execute('INSERT INTO portfolio(symbol,quantity,avg_price,updated_at) VALUES(?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET quantity=excluded.quantity,avg_price=excluded.avg_price,updated_at=excluded.updated_at',(symbol,float(quantity),float(avg_price),now_iso()))

def remove_position(symbol):
    initialize();
    with connect() as c:c.execute('DELETE FROM portfolio WHERE symbol=?',(symbol.upper(),))

def get_positions():
    initialize();
    with connect() as c:return [dict(x) for x in c.execute('SELECT * FROM portfolio ORDER BY symbol').fetchall()]

def _price(symbol):
    pair=symbol if symbol.endswith('USDT') else symbol+'USDT'; return float(requests.get('https://api.binance.com/api/v3/ticker/price',params={'symbol':pair},timeout=(5,15)).json()['price'])

def portfolio_report():
    rows=get_positions(); lines=['<b>💼 PORTFOLIO MANAGER</b>','']
    if not rows:return '\n'.join(lines+['Портфель пуст.','Добавить: <code>/portfolio_add BTC 0.1 60000</code>'])
    total=0; enriched=[]
    for r in rows:
        try:p=_price(r['symbol']); value=p*r['quantity']; pnl=(p/r['avg_price']-1)*100 if r['avg_price'] else 0
        except Exception:p=value=pnl=0
        total+=value; enriched.append((r,p,value,pnl))
    max_share=max((v for _,_,v,_ in enriched),default=0)/(total or 1)*100
    risk='HIGH' if max_share>65 else 'MEDIUM' if max_share>40 else 'BALANCED'
    lines += [f"Стоимость: <b>${total:,.2f}</b>",f"Концентрационный риск: <b>{risk}</b> (макс. доля {max_share:.1f}%)",'']
    for r,p,v,pnl in enriched: lines.append(f"<b>{r['symbol']}</b>: {r['quantity']:g} | ${v:,.2f} | PnL {pnl:+.1f}%")
    return '\n'.join(lines)
