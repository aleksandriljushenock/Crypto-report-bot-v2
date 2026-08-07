import html
import os
from datetime import datetime


def esc(value):
    return html.escape(str(value if value is not None else ''))


def compact_number(value):
    try:
        value = float(value)
    except Exception:
        return 'N/A'
    if value >= 1_000_000_000:
        return f'{value / 1_000_000_000:.2f}B'
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f}M'
    return f'{value:.2f}'


def build_signal_block(signal, index=None):
    direction = signal.get('direction', 'NO_TRADE')
    arrow = '📈 LONG' if direction == 'LONG_BIAS' else ('📉 SHORT' if direction == 'SHORT_BIAS' else '➖')
    title = f"{index}. " if index is not None else ''
    lines = [
        f"<b>🔥 {title}{esc(signal.get('symbol'))} — {arrow}</b>",
        f"Статус: <b>{esc(signal.get('status'))}</b> | Score: <b>{esc(signal.get('score'))}/100</b>",
        f"Вероятность: <b>{esc(signal.get('probability'))}%</b> | Уверенность: <b>{esc(signal.get('confidence'))}%</b>",
        f"🧠 AI Score: <b>{esc(signal.get('aiScore', 'N/A'))}/100</b> | Tier: <b>{esc(signal.get('aiTier', 'N/A'))}</b>",
        f"Сетап: <b>{esc(signal.get('setup'))}</b> | R/R: <b>{esc(signal.get('rr'))}</b>",
        f"Вход: <code>{esc(signal.get('entryText'))}</code>",
        f"Stop: <code>{esc(signal.get('stop'))}</code>",
        f"TP: <code>{esc(signal.get('tp1'))} / {esc(signal.get('tp2'))} / {esc(signal.get('tp3'))}</code>",
        f"1H/15M: {esc(signal.get('structure1h'))} / {esc(signal.get('structure15m'))}",
        f"Alignment: <b>{esc(signal.get('alignment'))}%</b> | TF: 1D {esc((signal.get('timeframes') or {}).get('1d'))}, 4H {esc((signal.get('timeframes') or {}).get('4h'))}, 1H {esc((signal.get('timeframes') or {}).get('1h'))}, 15M {esc((signal.get('timeframes') or {}).get('15m'))}, 5M {esc((signal.get('timeframes') or {}).get('5m'))}",
        f"Vol: {compact_number(signal.get('quoteVolume'))} | Taker: {esc(signal.get('takerRatio'))} | Funding: {esc(signal.get('fundingPercent'))}%",
    ]
    venues = [str(v).upper() for v in (signal.get('marketExchanges') or []) if v]
    if venues:
        total_venues = max(1, len([x for x in os.getenv('TRADE_MARKET_PROVIDERS', 'binance,bybit,okx,bitget,gate').split(',') if x.strip()]))
        filled = min(5, round(5 * len(venues) / total_venues))
        stars = '★' * filled + '☆' * (5 - filled)
        lines.append(f"Биржи: <b>{', '.join(esc(v) for v in venues[:5])}</b> ({len(venues)}/{total_venues}) · Coverage {stars}")
    if signal.get('qualityScore') is not None:
        lines.append(
            f"💎 Quality: <b>{esc(signal.get('qualityScore'))}/100</b> | "
            f"EV: <b>{float(signal.get('expectedValuePct') or 0):+.2f}%</b> | "
            f"P(cal): <b>{esc(signal.get('calibratedProbability'))}%</b>"
        )
        lines.append(f"Решение: <b>{esc(signal.get('qualityDecision'))}</b>")
        if signal.get('suggestedPositionSizeUsd') is not None and float(signal.get('suggestedPositionSizeUsd') or 0) > 0:
            lines.append(f"Рекомендуемый размер позиции: <b>${float(signal.get('suggestedPositionSizeUsd')):.2f}</b>")
        positives = signal.get('positiveProfileHits') or []
        negatives = signal.get('antiProfileHits') or []
        if positives:
            lines.append('Профили прибыли: ' + ', '.join(esc(x) for x in positives[:4]))
        if negatives:
            lines.append('Анти-профили: ' + ', '.join(esc(x) for x in negatives[:3]))
    profile = signal.get('tradeProfile') or {}
    if profile:
        lines.append(
            f"Профиль: Trend {esc(profile.get('trend'))} | Momentum {esc(profile.get('momentum'))} | "
            f"Volume {esc(profile.get('volume'))} | Risk {esc(profile.get('risk'))}"
        )
    listing = signal.get('listing') or {}
    if listing.get('isRecentListing'):
        lines.append(f"🆕 Недавний листинг: {esc(listing.get('listingAgeDays'))} дней | Alpha: {esc(listing.get('listingScore'))}")
    confirmations = signal.get('confirmations') or []
    if confirmations:
        lines.append('<b>Подтверждения:</b>')
        lines.extend(f"✓ {esc(x)}" for x in confirmations[:5])
    risks = signal.get('risks') or []
    if risks:
        lines.append('<b>Риски/условия:</b>')
        lines.extend(f"• {esc(x)}" for x in risks[:3])
    symbol = signal.get('symbol')
    lines.append(
        f'<a href="https://www.binance.com/en/futures/{esc(symbol)}">Binance</a> | '
        f'<a href="https://www.tradingview.com/chart/?symbol=BINANCE:{esc(symbol)}">TradingView</a>'
    )
    return '\n'.join(lines)


def build_trade_scan_report(result, manual=True):
    signals = result.get('signals', [])
    lines = [
        '<b>🔍 ТОРГОВЫЙ СКАНЕР</b>',
        f"UTC: {esc(result.get('runTimeUtc'))}",
        f"Проверено: <b>{result.get('rowsAnalyzed', 0)}</b> монет",
    ]
    stages = result.get('scannerStages') or {}
    if stages:
        lines += ['', '<b>Воронка отбора:</b>',
                  f"Структура/статус: <b>{stages.get('status', 0)}</b>",
                  f"Score: <b>{stages.get('score', 0)}</b>",
                  f"R/R: <b>{stages.get('rr', 0)}</b>",
                  f"Probability: <b>{stages.get('probability', 0)}</b>",
                  f"Quality: <b>{stages.get('quality', 0)}</b>",
                  f"EV: <b>{stages.get('ev', 0)}</b>"]
    if not signals:
        lines += ['', '<b>Подходящих входов сейчас нет.</b>']
        misses = result.get('nearMisses') or []
        if misses:
            lines += ['', '<b>Ближе всех:</b>']
            for item in misses[:3]:
                lines.append(
                    f"• {esc(item.get('symbol'))}: {esc(item.get('reason') or 'filter')} · "
                    f"Score {esc(item.get('score'))} · P {esc(item.get('probability'))}% · "
                    f"Q {esc(item.get('qualityScore') or '—')} · EV {esc(item.get('expectedValuePct') or '—')}"
                )
        return '\n'.join(lines)
    lines += ['', f"Найдено сетапов: <b>{len(signals)}</b>", '━━━━━━━━━━━━━━━━━━━━']
    for i, signal in enumerate(signals, 1):
        lines.append(build_signal_block(signal, i))
        lines.append('━━━━━━━━━━━━━━━━━━━━')
    lines.append('Риск на сделку: 0.5–1% капитала. Сигнал требует ручного подтверждения.')
    return '\n'.join(lines)


def build_monitor_status(settings, thread_alive=False, last_run=None, last_error=None):
    enabled = bool(settings.get('enabled'))
    lines = [
        '<b>📡 СТАТУС МОНИТОРИНГА</b>', '',
        f"Состояние: <b>{'ВКЛЮЧЕН' if enabled else 'ОСТАНОВЛЕН'}</b>",
        f"Поток: <b>{'работает' if thread_alive else 'не запущен'}</b>",
        f"Интервал: <b>{settings.get('interval_minutes', 15)} минут</b>",
    ]
    if last_run:
        lines.append(f"Последний цикл: {esc(last_run)}")
    if last_error:
        lines.append(f"Последняя ошибка: <code>{esc(last_error)[:500]}</code>")
    return '\n'.join(lines)


def build_recent_signals_report(rows):
    lines = ['<b>🔥 ПОСЛЕДНИЕ СИГНАЛЫ</b>']
    if not rows:
        lines += ['', 'История пока пуста.']
        return '\n'.join(lines)
    for i, row in enumerate(rows, 1):
        payload = row.get('payload') or {}
        lines += ['', build_signal_block(payload, i)]
        lines.append(f"Создан: {esc(row.get('created_at'))}")
        lines.append('━━━━━━━━━━━━━━━━━━━━')
    return '\n'.join(lines)
