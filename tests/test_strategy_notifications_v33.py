from strategies import notifications


def _row(state="waiting_entry", strategy="fib_05_pullback"):
    return {
        "id": 7,
        "strategy": strategy,
        "fingerprint": "fp-1",
        "symbol": "ETHUSDT",
        "direction": "LONG",
        "state": state,
        "entry_price": 3200.0,
        "stop_price": 3100.0,
        "tp_price": 3500.0,
        "rr": 3.0,
        "score": 84,
        "payload": {"entry_mode": "LIMIT", "reason": "test confluence"},
        "entered_at": "2026-08-14T10:00:00+00:00",
        "resolved_at": "2026-08-14T12:00:00+00:00",
        "outcome": "TP",
        "return_pct": 9.375,
    }


def test_ready_render_has_full_trade_levels():
    text = notifications.render_notification("READY", _row())
    assert "STRATEGY READY" in text
    assert "ETHUSDT" in text
    assert "Entry:" in text and "SL:" in text and "TP:" in text
    assert "готова к торговле" in text


def test_open_and_close_render():
    assert "ENTRY FILLED" in notifications.render_notification("OPEN", _row("open"))
    assert "STRATEGY CLOSED" in notifications.render_notification("CLOSED", _row("won"))
    assert "+9.38%" in notifications.render_notification("CLOSED", _row("won"))


def test_dispatch_marks_only_after_send(monkeypatch):
    rows = [{"event_type": "READY", "setup": _row()}]
    marked = []
    monkeypatch.setattr(notifications.repository, "pending_notifications", lambda **kwargs: rows)
    monkeypatch.setattr(notifications.repository, "mark_notification_sent", lambda setup_id, event: marked.append((setup_id, event)))
    monkeypatch.setattr(notifications, "boolean", lambda key, default=True: True)
    monkeypatch.setattr(notifications, "integer", lambda key, default, **kwargs: default)
    sent = []
    result = notifications.dispatch_pending_notifications(lambda chat, text: sent.append((chat, text)), 123)
    assert result["sent"] == 1
    assert len(sent) == 1
    assert marked == [(7, "READY")]


def test_dispatch_retries_on_send_failure(monkeypatch):
    rows = [{"event_type": "READY", "setup": _row()}]
    marked = []
    monkeypatch.setattr(notifications.repository, "pending_notifications", lambda **kwargs: rows)
    monkeypatch.setattr(notifications.repository, "mark_notification_sent", lambda setup_id, event: marked.append((setup_id, event)))
    monkeypatch.setattr(notifications, "boolean", lambda key, default=True: True)
    monkeypatch.setattr(notifications, "integer", lambda key, default, **kwargs: default)
    def fail(chat, text):
        raise RuntimeError("telegram down")
    result = notifications.dispatch_pending_notifications(fail, 123)
    assert result["sent"] == 0 and result["errors"] == 1
    assert marked == []
