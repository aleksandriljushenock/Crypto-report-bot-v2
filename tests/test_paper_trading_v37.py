from repositories import paper_repository as pr


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []
        self._limit = None
        self._desc = False
        self._order = None

    def select(self, *args, **kwargs): return self
    def eq(self, key, value):
        self.filters.append((key, value)); return self
    def order(self, key, desc=False):
        self._order = key; self._desc = desc; return self
    def limit(self, n):
        self._limit = n; return self
    def execute(self):
        rows = self.rows
        for key, value in self.filters:
            rows = [r for r in rows if r.get(key) == value]
        if self._order:
            rows = sorted(rows, key=lambda r: str(r.get(self._order) or ''), reverse=self._desc)
        if self._limit is not None:
            rows = rows[:self._limit]
        return _Resp(rows)


class _Client:
    def __init__(self, positions, trades):
        self.positions = positions
        self.trades = trades
    def table(self, name):
        if name == 'paper_positions': return _Query(self.positions)
        if name == 'paper_trades': return _Query(self.trades)
        raise AssertionError(name)


def _closed(pid='p1', fp='f1', pnl=2.0, closed_at='2026-08-21T10:00:00+00:00'):
    return {
        'id': pid, 'account_id': 'main', 'fingerprint': fp, 'symbol': 'BTCUSDT', 'side': 'LONG',
        'status': 'closed', 'entry_price': 100.0, 'exit_price': 102.0, 'stop_price': 95.0,
        'tp1_price': 110.0, 'margin_usd': 10.0, 'leverage': 10, 'notional_usd': 100.0,
        'entry_fee': 0.06, 'gross_pnl': 2.0, 'net_pnl': pnl, 'quality_score': 80.0,
        'probability': 75.0, 'expected_value_pct': 2.0, 'signal_payload': {},
        'opened_at': '2026-08-21T09:00:00+00:00', 'closed_at': closed_at,
        'updated_at': closed_at, 'close_reason': 'TP1', 'strategy_version': 'v',
        'fill_price_source': 'candle_trigger',
    }


def test_recent_trades_synthesizes_missing_ledger_row(monkeypatch):
    monkeypatch.setattr(pr, '_client', lambda: _Client([_closed()], []))
    rows = pr.PaperRepository().recent_trades(20)
    assert len(rows) == 1
    assert rows[0]['position_id'] == 'p1'
    assert rows[0]['net_pnl'] == 2.0
    assert rows[0]['_synthetic_from_position'] is True


def test_recent_trades_does_not_duplicate_existing_ledger(monkeypatch):
    position = _closed()
    trade = {
        'position_id': 'p1', 'fingerprint': 'f1', 'symbol': 'BTCUSDT', 'side': 'LONG',
        'net_pnl': 2.0, 'closed_at': position['closed_at'],
    }
    monkeypatch.setattr(pr, '_client', lambda: _Client([position], [trade]))
    rows = pr.PaperRepository().recent_trades(20)
    assert len(rows) == 1
    assert not rows[0].get('_synthetic_from_position')


def test_paper_win_rate_excludes_breakeven(monkeypatch):
    import paper_trading as pt
    monkeypatch.setattr(pt, 'ensure_account', lambda: {'initial_balance': 100.0, 'balance': 100.0, 'equity': 100.0})
    monkeypatch.setattr(pt, 'get_recent_trades', lambda limit=20: [
        {'net_pnl': 1.0, 'close_reason': 'TP1'},
        {'net_pnl': -1.0, 'close_reason': 'SL'},
        {'net_pnl': 0.0, 'close_reason': 'TIME_EXIT'},
    ])
    monkeypatch.setattr(pt, '_open_positions', lambda: [])
    monkeypatch.setattr(pt, '_pending_positions', lambda: [])
    out = pt.performance()
    assert out['wins'] == 1 and out['losses'] == 1 and out['breakeven'] == 1
    assert out['win_rate'] == 50.0


def test_close_still_returns_trade_when_ledger_write_fails(monkeypatch):
    import paper_trading as pt
    position = {
        'id':'p1','fingerprint':'f1','symbol':'BTCUSDT','side':'LONG','status':'open',
        'entry_price':100.0,'stop_price':95.0,'tp1_price':110.0,'notional_usd':100.0,
        'margin_usd':10.0,'entry_fee':0.06,'leverage':10,'opened_at':'2026-08-21T09:00:00+00:00',
        'execution_audit':{},
    }
    monkeypatch.setattr(pt.paper_repo, 'update_position', lambda *a, **k: {'id':'p1'})
    def boom(_row):
        raise RuntimeError('temporary ledger outage')
    monkeypatch.setattr(pt.paper_repo, 'upsert_trade', boom)
    monkeypatch.setattr(pt, 'ensure_account', lambda: {'balance':89.94,'equity':99.94,'realized_pnl':0.0,'fees_paid':0.06})
    monkeypatch.setattr(pt.paper_repo, 'update_account', lambda *a, **k: None)
    monkeypatch.setattr(pt.paper_repo, 'close_atomic', lambda **kw: {'balance_after': 100.0})
    out = pt._close_position(position, 110.0, 'TP1')
    assert out['symbol'] == 'BTCUSDT'
    assert out['net_pnl'] > 0
