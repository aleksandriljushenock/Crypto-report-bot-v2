"""v22.2 near-signal watchlist.

Only stores *real* near candidates: a symbol must miss exactly one executable gate
and be sufficiently close to that gate. Candidates missing multiple gates remain
available to Shadow Signals but do not consume frequent near-watch rescans.
"""
from __future__ import annotations

from core.runtime_config import boolean, integer, number, raw

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path('data') / 'near_signal_watchlist.db'


def _now():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _now()).isoformat()


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialize():
    with _conn() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS near_signals (
            symbol TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            next_check_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            reason TEXT,
            score REAL,
            probability REAL,
            quality REAL,
            ev REAL,
            source TEXT,
            payload_json TEXT NOT NULL
        )''')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(near_signals)').fetchall()}
        migrations = {
            'distance_score': 'REAL',
            'missing_gate': 'TEXT',
            'current_value': 'REAL',
            'threshold_value': 'REAL',
        }
        for name, sql_type in migrations.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE near_signals ADD COLUMN {name} {sql_type}')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_near_due ON near_signals(next_check_at, expires_at)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_near_distance ON near_signals(distance_score DESC, next_check_at)')
        # v22.2 semantics changed: legacy rows were admitted by a much broader rule
        # and have no reliable distance metadata. Drop them once instead of showing
        # stale weak candidates after deployment.
        conn.execute("DELETE FROM near_signals WHERE distance_score IS NULL OR missing_gate IS NULL OR missing_gate = ''")


def _float(value):
    try:
        if value is None or value == '':
            return None
        return float(value)
    except Exception:
        return None


def _reason_parts(item):
    raw = str(item.get('reason') or '')
    return [x.strip() for x in raw.split(',') if x.strip()]


def _threshold_for_gate(item, gate):
    profile_thresholds = item.get('profileThresholds') or {}
    profile = str(item.get('signalProfile') or item.get('setup') or '').upper()

    def env_profile(suffix, base_name, default):
        if profile:
            value = raw(f'{base_name}_{profile}_{suffix}', None)
            if value not in (None, ''):
                return _float(value)
        return _float(raw(f'{base_name}_{suffix}', default))

    if gate == 'Score':
        return _float(profile_thresholds.get('score') or item.get('scoreThreshold')) or env_profile('MIN_SCORE', 'TRADE', 72)
    if gate == 'R/R':
        return _float(profile_thresholds.get('rr') or item.get('rrThreshold')) or env_profile('MIN_RR', 'TRADE', 2.0)
    if gate == 'Probability':
        return _float(profile_thresholds.get('probability') or item.get('probabilityThreshold')) or env_profile('MIN_PROBABILITY', 'TRADE', 65)
    if gate == 'Quality':
        return _float(item.get('profileQualityThreshold') or item.get('qualityThreshold')) or env_profile('MIN_QUALITY', 'HEDGE', 70)
    if gate == 'EV':
        return _float(item.get('profileEvThreshold') or item.get('evThreshold')) or env_profile('MIN_EV_PCT', 'HEDGE', 2.0)
    return None


def _value_for_gate(item, gate):
    if gate == 'Score':
        return _float(item.get('score'))
    if gate == 'R/R':
        return _float(item.get('rr'))
    if gate == 'Probability':
        return _float(item.get('probability'))
    if gate == 'Quality':
        return _float(item.get('qualityScore'))
    if gate == 'EV':
        return _float(item.get('expectedValuePct'))
    return None


def classify_near_candidate(item):
    """Return enriched near-candidate metadata or None.

    A real near candidate must miss exactly one gate and be within the configured
    distance of its threshold. anti-profile/hard-block is intentionally not a
    near gate because it usually needs a structural regime change, not a small
    numerical move.
    """
    if not isinstance(item, dict):
        return None
    parts = _reason_parts(item)
    numeric_gates = {'Score', 'R/R', 'Probability', 'Quality', 'EV'}
    failed_numeric = [x for x in parts if x in numeric_gates]
    non_numeric = [x for x in parts if x not in numeric_gates]
    if len(failed_numeric) != 1 or non_numeric:
        return None
    gate = failed_numeric[0]
    current = _value_for_gate(item, gate)
    threshold = _threshold_for_gate(item, gate)
    if current is None or threshold is None or threshold <= 0:
        return None
    # It must truly be below the gate; stale/ambiguous diagnostic rows are not near.
    if current >= threshold:
        return None
    distance = max(0.0, min(100.0, current / threshold * 100.0))
    min_distance = float(str(number('NEAR_SIGNAL_MIN_DISTANCE_PCT', 85.0)))
    if distance < min_distance:
        return None

    enriched = dict(item)
    enriched['nearDistanceScore'] = round(distance, 2)
    enriched['nearMissingGate'] = gate
    enriched['nearCurrentValue'] = current
    enriched['nearThresholdValue'] = threshold
    return enriched


def select_near_candidates(items):
    selected = []
    for item in items or []:
        candidate = classify_near_candidate(item)
        if candidate:
            selected.append(candidate)
    selected.sort(
        key=lambda x: (
            float(x.get('nearDistanceScore') or 0),
            float(x.get('probability') or 0),
            float(x.get('qualityScore') or -1),
            float(x.get('score') or 0),
        ),
        reverse=True,
    )
    return selected


def upsert_near_candidates(items, source='scan'):
    initialize()
    selected = select_near_candidates(items)
    ttl_hours = max(1.0, number('NEAR_SIGNAL_TTL_HOURS', 12.0, minimum=1.0))
    check_minutes = max(1, int(float(str(integer('NEAR_SIGNAL_RESCAN_MINUTES', 5)))))
    now = _now()
    count = 0
    with _conn() as conn:
        conn.execute('DELETE FROM near_signals WHERE expires_at < ?', (_iso(now),))
        for item in selected:
            symbol = str(item.get('symbol') or '').upper()
            if not symbol:
                continue
            quality = _float(item.get('qualityScore'))
            ev = _float(item.get('expectedValuePct'))
            row = (
                symbol, _iso(now), _iso(now), _iso(now + timedelta(minutes=check_minutes)),
                _iso(now + timedelta(hours=ttl_hours)), str(item.get('reason') or ''),
                float(item.get('score') or 0), float(item.get('probability') or 0),
                quality, ev, source, json.dumps(item, ensure_ascii=False),
                float(item.get('nearDistanceScore') or 0), str(item.get('nearMissingGate') or ''),
                _float(item.get('nearCurrentValue')), _float(item.get('nearThresholdValue')),
            )
            conn.execute('''INSERT INTO near_signals (
                symbol, first_seen, last_seen, next_check_at, expires_at, reason,
                score, probability, quality, ev, source, payload_json,
                distance_score, missing_gate, current_value, threshold_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                last_seen=excluded.last_seen,
                next_check_at=excluded.next_check_at,
                expires_at=excluded.expires_at,
                reason=excluded.reason,
                score=excluded.score,
                probability=excluded.probability,
                quality=excluded.quality,
                ev=excluded.ev,
                source=excluded.source,
                payload_json=excluded.payload_json,
                distance_score=excluded.distance_score,
                missing_gate=excluded.missing_gate,
                current_value=excluded.current_value,
                threshold_value=excluded.threshold_value
            ''', row)
            count += 1
    return count


def get_due_symbols(limit=None):
    initialize()
    limit = max(1, int(limit or str(integer('NEAR_SIGNAL_RESCAN_LIMIT', 40))))
    now = _iso()
    with _conn() as conn:
        conn.execute('DELETE FROM near_signals WHERE expires_at < ?', (now,))
        rows = conn.execute('''SELECT symbol FROM near_signals
            WHERE next_check_at <= ? AND expires_at >= ?
            ORDER BY distance_score DESC, probability DESC, score DESC, last_seen DESC
            LIMIT ?''', (now, now, limit)).fetchall()
    return [r['symbol'] for r in rows]


def mark_checked(symbols, promoted=None, retained=None):
    """Update a targeted rescan.

    promoted symbols leave the watchlist because they became signals. Symbols no
    longer classified as near are also removed instead of remaining as stale rows.
    """
    initialize()
    promoted = {str(x).upper() for x in (promoted or [])}
    retained = None if retained is None else {str(x).upper() for x in retained}
    minutes = max(1, int(float(str(integer('NEAR_SIGNAL_RESCAN_MINUTES', 5)))))
    next_time = _iso(_now() + timedelta(minutes=minutes))
    with _conn() as conn:
        for symbol in symbols or []:
            symbol = str(symbol).upper()
            if symbol in promoted or (retained is not None and symbol not in retained):
                conn.execute('DELETE FROM near_signals WHERE symbol=?', (symbol,))
            else:
                conn.execute('UPDATE near_signals SET next_check_at=? WHERE symbol=?', (next_time, symbol))


def get_rows(limit=10):
    initialize()
    now = _iso()
    with _conn() as conn:
        conn.execute('DELETE FROM near_signals WHERE expires_at < ?', (now,))
        rows = conn.execute('''SELECT * FROM near_signals
            ORDER BY distance_score DESC, probability DESC, score DESC
            LIMIT ?''', (max(1, int(limit)),)).fetchall()
    return [dict(x) for x in rows]
