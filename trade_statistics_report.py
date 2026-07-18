import html


def esc(value):
    return html.escape(str(value if value is not None else ''))


def build_watchlist_report(rows):
    lines = ['<b>⭐ AI WATCHLIST</b>']
    if not rows:
        return '\n'.join(lines + ['', 'Watchlist пока пуст.'])
    for index, row in enumerate(rows, 1):
        payload = row.get('payload') or {}
        lines += [
            '',
            f"<b>{index}. {esc(row.get('symbol'))}</b> — {esc(row.get('direction'))}",
            f"Score: <b>{row.get('last_score', 0):.0f}</b> | Вероятность: <b>{row.get('last_probability', 0):.0f}%</b>",
            f"Статус: {esc(row.get('status'))} | Лучший Score: {row.get('best_score', 0):.0f}",
        ]
        narrative = (payload.get('listing') or {}).get('listingScore')
        if narrative is not None:
            lines.append(f"Listing Alpha: {esc(narrative)}")
    return '\n'.join(lines)


def build_performance_report(rows):
    lines = ['<b>📈 ЭФФЕКТИВНОСТЬ СИГНАЛОВ</b>']
    if not rows:
        return '\n'.join(lines + ['', 'Результатов пока недостаточно.'])
    for row in rows:
        count = int(row.get('count') or 0)
        wins = int(row.get('wins') or 0)
        winrate = wins / count * 100 if count else 0
        lines += [
            '',
            f"<b>{esc(row.get('horizon'))}</b>",
            f"Наблюдений: {count} | Win rate: {winrate:.1f}%",
            f"Средний результат: {float(row.get('avg_return') or 0):+.2f}%",
            f"TP hits: {int(row.get('tp_hits') or 0)} | SL hits: {int(row.get('sl_hits') or 0)}",
        ]
    return '\n'.join(lines)
