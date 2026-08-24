from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _features(base=50):
    return {
        'trend': base, 'momentum': base, 'volume': base, 'funding': base,
        'open_interest': base, 'alignment': base, 'risk_reward': base,
        'capital_flow': base, 'narrative': base, 'news': base, 'smart_money': base,
    }


def test_learning_max2_uses_calibrated_tuple_and_active_model(monkeypatch):
    import learning_max2 as lm
    model = {'version': 'test', 'weights': {k: 1.0 for k in lm.DEFAULT_WEIGHTS}, 'config': {}}
    monkeypatch.setattr(lm, 'active_model', lambda defaults: model)
    monkeypatch.setattr(lm, 'specialist_weights', lambda model, regime, direction: model['weights'])
    called = {}
    def fake_cal(score, regime, received_model):
        called['model'] = received_model
        return 0.77, 0.11
    monkeypatch.setattr(lm, 'calibrated_probability', fake_cal)
    pred = lm.predict(_features(70), 'LONG_BIAS')
    assert pred['calibrated_v14_probability'] == 77.0
    assert 70.0 < pred['probability'] < 77.0
    assert pred['uncertainty'] >= 11.0
    assert called['model'] is model


def test_learning_max2_operator_weights_affect_final_probability(monkeypatch):
    import learning_max2 as lm
    factors = _features(50)
    factors['trend'] = 95
    model = {'version': 'test', 'weights': {k: 1.0 for k in lm.DEFAULT_WEIGHTS}, 'config': {}}
    monkeypatch.setattr(lm, 'active_model', lambda defaults: model)
    monkeypatch.setattr(lm, 'calibrated_probability', lambda score, regime, model: (score / 100.0, 0.1))
    monkeypatch.setattr(lm, 'specialist_weights', lambda model, regime, direction: {k: (3.0 if k == 'trend' else 0.2) for k in lm.DEFAULT_WEIGHTS})
    high = lm.predict(factors, 'LONG')
    monkeypatch.setattr(lm, 'specialist_weights', lambda model, regime, direction: {k: (0.1 if k == 'trend' else 1.0) for k in lm.DEFAULT_WEIGHTS})
    low = lm.predict(factors, 'LONG')
    assert high['weighted_v14_score'] > low['weighted_v14_score']
    assert high['probability'] > low['probability']


def test_adaptive_scheduled_training_obeys_model_control(monkeypatch):
    import adaptive_model_manager as amm
    import model_control
    monkeypatch.setattr(model_control, 'auto_learning_enabled', lambda: False)
    monkeypatch.setattr(amm, '_load_rows', lambda limit: (_ for _ in ()).throw(AssertionError('must not load rows')))
    result = amm.train_candidate('scheduled')
    assert result['status'] == 'disabled-by-runtime-setting'


def test_background_optimizer_does_not_train_adaptive_when_auto_off(monkeypatch):
    import background_services as bs
    import model_control
    monkeypatch.setattr(model_control, 'auto_learning_enabled', lambda: False)
    monkeypatch.setattr(bs, 'run_optimizer', lambda trigger='scheduled': {'samples': 1, 'recommendations_count': 0})
    monkeypatch.setattr(bs, 'train_candidate', lambda trigger='scheduled': (_ for _ in ()).throw(AssertionError('must not train')))
    obj = object.__new__(bs.AutomationSupervisor)
    obj.logger = lambda text: None
    result = obj._run_optimizer_models()
    assert result['adaptive_model']['status'] == 'disabled-by-runtime-setting'


def test_discovery_callback_is_wired(monkeypatch):
    import telegram_ui.router as r
    sent = []
    monkeypatch.setattr(r, 'log', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(r, 'is_authorized', lambda chat_id: True, raising=False)
    monkeypatch.setattr(r, 'telegram_request', lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(r, 'get_monitor_settings', lambda: {}, raising=False)
    monkeypatch.setattr(r, 'handle_system_callback', lambda *a, **k: False, raising=False)
    monkeypatch.setattr(r, '_chronos_state_text', lambda: '', raising=False)
    monkeypatch.setattr(r, 'trade_monitor', None, raising=False)
    monkeypatch.setattr(r, 'trade_scan_thread', None, raising=False)
    monkeypatch.setattr(r, 'report_thread', None, raising=False)
    monkeypatch.setattr(r, 'new_scan_thread', None, raising=False)
    monkeypatch.setattr(r, 'send_message', lambda chat_id, text, **kw: sent.append((chat_id, text, kw)), raising=False)
    update = {'callback_query': {'id': '1', 'data': 'menu_discovery', 'message': {'chat': {'id': 42}}}}
    r._process_update(update)
    assert sent and sent[0][0] == 42
    assert 'DISCOVERY' in sent[0][1]
    assert sent[0][2].get('reply_markup')


def test_profit_profile_builder_creates_multiple_recent_windows():
    from build_profit_profile import build
    now = datetime.now(timezone.utc)
    rows = []
    for idx, days in enumerate([2, 5, 10, 18, 28, 50, 100, 220] * 5):
        features = {
            'setup': 'PULLBACK', 'marketRegime': 'range', 'structure1h': 'BOS_UP',
            'structure15m': 'N/A', 'timeframes': {'1d':'UP','4h':'UP','1h':'UP','15m':'UP','5m':'UP'},
            'probability': 70, 'confidence': 60, 'uncertainty': 30, 'quoteVolume': 200_000_000, 'rr': 2.5,
            'aiFactors': _features(65),
        }
        rows.append({
            'symbol': 'BTCUSDT', 'signal_direction': 'LONG', 'signal_score': 75,
            'created_at': (now - timedelta(days=days)).isoformat(),
            'features': features, 'real_result': {'return_percent': 2 if idx % 2 == 0 else -1, 'success': idx % 2 == 0},
        })
    profile = build(rows, windows=(7, 21, 90))
    assert profile['recent_window_options'] == [7, 21, 90]
    assert set(profile['recent_windows']) == {'7', '21', '90'}
    assert profile['overall']['samples'] == len(rows)


def test_recency_runtime_window_and_half_life_affect_blend(monkeypatch):
    import ai_hedge_fund_engine as hedge
    hedge._CACHE = {
        'overall': {'win_rate': 50}, 'groups': {}, 'rules': [],
        'recent_windows': {
            '7': {'symbol': {'BTCUSDT': {'samples': 40, 'win_rate': 80}}},
            '30': {'symbol': {'BTCUSDT': {'samples': 80, 'win_rate': 60}}},
        },
    }
    vals = {'PROFILE_RECENT_WINDOW_DAYS': 7, 'PROFILE_MIN_RECENT_SAMPLES': 30, 'PROFILE_RECENT_WEIGHT': 2.0, 'PROFILE_HALF_LIFE_DAYS': 14}
    monkeypatch.setattr(hedge, '_env_int', lambda name, default: int(vals.get(name, default)))
    monkeypatch.setattr(hedge, '_env_float', lambda name, default: float(vals.get(name, default)))
    monkeypatch.setattr(hedge, '_env_bool', lambda name, default=False: True)
    short, meta_short = hedge._blend_recent_rate(50, 'symbol', 'BTCUSDT')
    vals['PROFILE_RECENT_WINDOW_DAYS'] = 30
    long, meta_long = hedge._blend_recent_rate(50, 'symbol', 'BTCUSDT')
    assert meta_short['window_days'] == 7
    assert meta_long['window_days'] == 30
    assert short > long
    vals['PROFILE_HALF_LIFE_DAYS'] = 3
    fast_decay, _ = hedge._blend_recent_rate(50, 'symbol', 'BTCUSDT')
    assert fast_decay < long


def test_paper_guard_has_independent_lock(monkeypatch):
    import background_services as bs
    obj = object.__new__(bs.AutomationSupervisor)
    obj.logger = lambda text: None
    # Occupy the heavy lock: paper guard must still execute.
    assert bs._HEAVY_TASK_LOCK.acquire(blocking=False)
    try:
        runner = obj._guarded('paper-test', lambda: {'status': 'ran'}, shared_heavy_lock=False)
        result = runner()
        assert result['status'] == 'ran'
    finally:
        bs._HEAVY_TASK_LOCK.release()
