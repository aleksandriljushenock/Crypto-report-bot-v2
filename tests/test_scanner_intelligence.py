from datetime import datetime, timezone
from pathlib import Path

import scanner_intelligence as si


def test_save_and_aggregate(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_DATA", tmp_path)
    monkeypatch.setattr(si, "_LAST", tmp_path / "last.json")
    monkeypatch.setattr(si, "_HISTORY", tmp_path / "history.jsonl")
    payload = {
        "runTimeUtc": datetime.now(timezone.utc).isoformat(),
        "stages": {"analyzed": 30, "status": 12, "score": 8, "rr": 6, "probability": 4, "quality": 2, "ev": 1, "signals": 1},
    }
    si.save_scan_intelligence(payload)
    loaded = si.get_last_scan_intelligence()
    assert loaded["stages"]["signals"] == 1
    agg = si.aggregate_24h()
    assert agg["scans"] == 1
    assert agg["analyzed"] == 30
    assert agg["signals"] == 1


def test_recommendation_points_to_ev_bottleneck():
    summary = {"stages": {"analyzed": 30, "probability": 5, "quality": 3, "ev": 0, "signals": 0}}
    assert "EV" in si.build_recommendation(summary)
