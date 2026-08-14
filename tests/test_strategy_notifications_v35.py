import os
from strategies.catalog import STRATEGIES
from strategy_settings import SPEC_BY_KEY, current_value
from telegram_ui.keyboards import strategy_lab_keyboard


def test_every_strategy_has_default_off_notification_setting(monkeypatch):
    for spec in STRATEGIES:
        key = f"STRATEGY_NOTIFY_{spec.key.upper()}"
        assert key in SPEC_BY_KEY
        monkeypatch.delenv(key, raising=False)
        assert current_value(key) == "false"


def test_strategy_keyboard_has_notification_toggle(monkeypatch):
    spec = STRATEGIES[0]
    key = f"STRATEGY_NOTIFY_{spec.key.upper()}"
    monkeypatch.delenv(key, raising=False)
    kb = strategy_lab_keyboard(spec.key)
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "🔕 Уведомления: ВЫКЛ" in labels
    monkeypatch.setenv(key, "true")
    kb = strategy_lab_keyboard(spec.key)
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "🔔 Уведомления: ВКЛ" in labels


def test_notifier_requires_per_strategy_opt_in(monkeypatch):
    import strategies.notifications as n
    monkeypatch.delenv("STRATEGY_NOTIFY_FIB_05_PULLBACK", raising=False)
    monkeypatch.setenv("STRATEGY_LAB_NOTIFY_ENABLED", "true")
    row = {"state":"waiting_entry", "strategy":"fib_05_pullback", "symbol":"BTCUSDT", "entry_price":1, "stop_price":.9, "tp_price":1.2, "payload":{"status":"READY"}}
    monkeypatch.setattr(n.repository, "pending_notifications", lambda **kw: [{"event_type":"READY","setup":row}])
    sent=[]
    out=n.dispatch_pending_notifications(lambda c,t: sent.append(t), 1)
    assert out["sent"] == 0 and sent == []
