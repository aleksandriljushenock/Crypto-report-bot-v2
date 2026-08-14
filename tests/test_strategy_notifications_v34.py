from strategies import notifications


def _row(status="READY", state="waiting_entry"):
    return {
        "id": 77,
        "strategy": "fib_05_pullback",
        "symbol": "ETHUSDT",
        "direction": "LONG",
        "state": state,
        "entry_price": 3200.0,
        "stop_price": 3100.0,
        "tp_price": 3500.0,
        "rr": 3.0,
        "score": 84,
        "payload": {"status": status, "entry_mode": "LIMIT", "reason": "confluence"},
        "entered_at": "2026-08-14T10:00:00+00:00",
        "resolved_at": "2026-08-14T12:00:00+00:00",
        "outcome": "TP",
        "return_pct": 9.375,
    }


def _patch_config(monkeypatch):
    monkeypatch.setattr(notifications, "boolean", lambda key, default=True: True)
    monkeypatch.setattr(notifications, "integer", lambda key, default, **kwargs: default)


def test_ready_message_is_actionable_not_waiting():
    text = notifications.render_notification("READY", _row())
    assert "STRATEGY READY" in text
    assert "готова к торговле" in text
    assert "ждём" not in text.lower()
    assert "Entry:" in text and "SL:" in text and "TP:" in text


def test_watch_and_waiting_analysis_are_never_notifiable():
    assert notifications._is_notifiable("READY", _row("WATCH")) is False
    assert notifications._is_notifiable("READY", _row("WAITING")) is False
    assert notifications._is_notifiable("READY", _row("NO_SETUP")) is False
    assert notifications._is_notifiable("READY", _row("READY")) is True


def test_dispatch_suppresses_watch_waiting(monkeypatch):
    rows = [
        {"event_type": "READY", "setup": _row("WATCH")},
        {"event_type": "READY", "setup": _row("WAITING")},
        {"event_type": "READY", "setup": _row("READY")},
    ]
    monkeypatch.setattr(notifications.repository, "pending_notifications", lambda **kwargs: rows)
    marked = []
    monkeypatch.setattr(notifications.repository, "mark_notification_sent", lambda setup_id, event: marked.append((setup_id, event)))
    _patch_config(monkeypatch)
    sent = []
    result = notifications.dispatch_pending_notifications(lambda chat, text: sent.append(text), 123)
    assert result["sent"] == 1
    assert len(sent) == 1 and "STRATEGY READY" in sent[0]
    assert marked == [(77, "READY")]


def test_open_closed_lifecycle_still_allowed():
    assert notifications._is_notifiable("OPEN", _row("READY", "open")) is True
    assert notifications._is_notifiable("CLOSED", _row("READY", "won")) is True
    assert notifications._is_notifiable("CLOSED", _row("READY", "lost")) is True
