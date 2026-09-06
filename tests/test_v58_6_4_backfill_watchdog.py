from datetime import datetime, timezone, timedelta
from pathlib import Path


def test_version_5864():
    assert Path('VERSION').read_text().strip() == '58.6.4'


def test_incremental_backfill_fresh_terminal_skip():
    import backfill_execution_dataset_v57 as b
    now=datetime.now(timezone.utc)
    current={'label_version':b.CURRENT_SHADOW_LABEL,'entry_status':'filled','outcome':'TP1','updated_at':now.isoformat()}
    assert b._sample_is_fresh(current, source_filled=True, retry_hours=6)
    unresolved={'label_version':b.CURRENT_SHADOW_LABEL,'entry_status':'unresolved','outcome':'UNRESOLVED','updated_at':(now-timedelta(hours=7)).isoformat()}
    assert not b._sample_is_fresh(unresolved, retry_hours=6)


def test_autonomous_worker_emits_backfill_progress_and_lease_after_backfill():
    text=Path('execution_auto_worker.py').read_text()
    assert 'progress_callback=_backfill_progress' in text
    assert 'incremental=True' in text
    assert text.index("backfill(limit=limit") < text.index("training_slot(owner='execution-auto-v58.6.4')")


def test_stage_specific_watchdog_present():
    text=Path('background_services.py').read_text()
    assert 'EXECUTION_STAGE_BACKFILL_TIMEOUT_SECONDS' in text
    assert 'EXECUTION_STAGE_BACKFILL_STALL_SECONDS' in text
    assert 'stage-stalled:' in text
    assert "processed={progress.get('processed')}" in text


def test_outcome_tracker_separates_market_unavailable():
    text=Path('outcome_tracker.py').read_text()
    assert 'outcome_failures' in text
    assert 'market_unavailable' in text
    assert 'except UnsupportedSymbolError' in text


def test_extra_symbols_are_admission_gated():
    text=Path('scanner/universe.py').read_text()
    assert 'known_tradable_any(symbol)' in text
    import trade_market_client as t
    assert hasattr(t, 'known_tradable_any')
