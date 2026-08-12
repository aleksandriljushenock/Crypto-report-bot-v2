import strategies.scheduler as scheduler


def test_parallel_scheduler_does_not_skip_main_scanner(monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_AUTO_ENABLED", "true")
    monkeypatch.setenv("STRATEGY_LAB_PARALLEL_WITH_MAIN", "true")
    monkeypatch.setenv("STRATEGY_LAB_AUTO_MODE", "round_robin")
    monkeypatch.setattr(scheduler, "is_trade_scan_running", lambda: True)
    monkeypatch.setattr(scheduler, "is_strategy_scan_running", lambda: False)
    monkeypatch.setattr(scheduler, "_next_spec", lambda: scheduler.STRATEGIES[0])
    monkeypatch.setattr(scheduler, "run_strategy_scan", lambda key, force_parallel_budget=False: {"summary": {"ready": 1, "watch": 2, "analyzed": 10, "parallel": force_parallel_budget}})
    result = scheduler.run_scheduled_cycle()
    assert result["status"] == "ok"
    assert result["runs"][0]["analyzed"] == 10


def test_parallel_scheduler_can_be_disabled(monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_AUTO_ENABLED", "true")
    monkeypatch.setenv("STRATEGY_LAB_PARALLEL_WITH_MAIN", "false")
    monkeypatch.setattr(scheduler, "is_trade_scan_running", lambda: True)
    monkeypatch.setattr(scheduler, "is_strategy_scan_running", lambda: False)
    result = scheduler.run_scheduled_cycle()
    assert result["status"] == "skipped-main-scanner"


def test_scheduler_status_exposes_parallel_mode(monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_PARALLEL_WITH_MAIN", "true")
    monkeypatch.setenv("STRATEGY_LAB_SYNC_WITH_MAIN", "true")
    st = scheduler.status()
    assert st["parallel_with_main"] is True
    assert st["sync_with_main"] is True
