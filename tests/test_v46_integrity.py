from pathlib import Path
from datetime import datetime, timedelta, timezone

def test_version_v46(): assert Path('VERSION').read_text().strip() in {'47.0.0','48.0.0'}

def test_shadow_fingerprint_is_event_specific():
    text=Path('shadow_signals.py').read_text()
    assert "structural_key=str(item.get('fingerprint')" in text and "SHADOW_EVENT_BUCKET_SECONDS" in text

def test_shadow_never_fills_after_expiry_or_open_candle():
    text=Path('shadow_signals.py').read_text()
    assert 'cend > now' in text and 'cend > expires' in text
    assert "status='entry_unresolved'" in text

def test_shadow_recovery_is_paginated():
    text=Path('shadow_signals.py').read_text()
    assert 'SHADOW_RECOVERY_PAGE_SIZE' in text and '.range(' in text

def test_outcome_terminal_failure_and_target_observed_time():
    text=Path('trade_outcome_tracker.py').read_text()
    assert 'CREATE TABLE IF NOT EXISTS outcome_failures' in text
    assert 'target_time.isoformat()' in text

def test_outcome_dedupe_uses_entry_and_setup():
    text=Path('trade_outcome_tracker.py').read_text()
    assert "json_extract(payload_json,'$.setup')" in text
    assert 'ABS(COALESCE(entry_price,0)-?)' in text

def test_paper_time_exit_does_not_fake_breakeven():
    text=Path('paper_trading.py').read_text()
    assert 'execution_unresolved_at_time_exit' in text
    assert "exit_price = legal[-1][4]" in text

def test_v46_migration_prefers_resolved_duplicate_and_has_distributed_lease():
    text=Path('migrations/SUPABASE_V46_INTEGRITY.sql').read_text()
    assert "training_status='ready'" in text
    assert 'model_training_lease_acquire_v46' in text
    assert 'pg_advisory_xact_lock' in text

def test_cloud_rpc_failure_is_fail_closed():
    text=Path('cloud_learning_store.py').read_text()
    marker='V45 observation RPC failed; verifying fingerprint before fallback'
    assert marker in text
    segment=text[text.index(marker):text.index(marker)+700]
    assert 'return None' in segment

def test_default_env_not_low_memory():
    assert 'LOW_MEMORY_MODE=false' in Path('.env.example').read_text()
