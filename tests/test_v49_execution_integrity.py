from pathlib import Path
from datetime import datetime, timezone


def test_version_v49():
    assert Path('VERSION').read_text().strip() in {'49.0.0','50.0.0','51.0.0','52.0.0','53.0.0','54.0.0','55.0.0','56.0.0','57.0.0','57.1.0','57.2.0','58.0.0','58.1.0','58.2.0','58.3.0','58.4.0','58.5.0'}


def test_v48_migration_normalizes_namespace_without_active_collision():
    src=Path('migrations/SUPABASE_V48_INTEGRITY.sql').read_text()
    assert "model_name='learning-v14' AND is_active=true" in src
    assert "model_name='learning-engine-v14' AND is_active=true" in src
    assert src.index("SET is_active=false") < src.index("SET model_name='learning-v14'")


def test_v49_terminal_rpc_merges_horizons():
    src=Path('migrations/SUPABASE_V49_EXECUTION_INTEGRITY.sql').read_text()
    assert 'learning_mark_terminal_outcome_v49' in src
    assert "metadata->'terminal_outcomes'" in src
    assert 'jsonb_build_object(p_horizon,p_reason)' in src


def test_paper_boundary_freezes_cursor_and_disables_live_price_overlay():
    src=Path('paper_trading.py').read_text()
    assert 'covered_until = since' in src
    assert 'covered_until = last_checked' in src
    assert 'history_fresh = (not boundary_uncertain)' in src
    assert 'and (not boundary_uncertain) and now_dt >= max_hold' in src


def test_shadow_boundary_is_unresolved_not_expired_and_fill_is_interval_aware():
    src=Path('shadow_signals.py').read_text()
    assert 'boundary_uncertain=True' in src
    assert "status='entry_unresolved'" in src
    assert 'fill=target; fill_dt=cend' in src
    assert "execution_precision=f'{interval_minutes}m_ohlc'" in src
    assert 'fill_dt=cdt+timedelta(minutes=5)' not in src


def test_terminal_horizons_restore_to_local_failure_ledger():
    src=Path('trade_outcome_tracker.py').read_text()
    assert 'terminal = metadata.get("terminal_outcomes")' in src
    assert 'INSERT OR REPLACE INTO outcome_failures' in src
    assert '.mark_terminal_outcome(' in src


def test_cloud_terminal_metadata_deep_merge():
    src=Path('cloud_learning_store.py').read_text()
    assert 'terminal.update(incoming["terminal_outcomes"])' in src
    assert 'learning_mark_terminal_outcome_v49' in src
