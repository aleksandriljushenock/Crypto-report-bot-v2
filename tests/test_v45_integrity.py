from __future__ import annotations
import os, subprocess, sys, textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_v46_version():
    assert Path('VERSION').read_text().strip() in {'47.0.0','48.0.0','49.0.0','50.0.0','51.0.0','52.0.0','53.0.0','54.0.0','55.0.0','56.0.0','57.0.0','57.1.0','57.2.0','58.0.0','58.1.0','58.2.0','58.3.0'}


def test_training_coordinator_is_cross_process(tmp_path):
    lock=tmp_path/'train.lock'
    env=dict(os.environ,MODEL_TRAINING_LOCK_FILE=str(lock),PYTHONPATH=str(Path.cwd()))
    holder=subprocess.Popen([sys.executable,'-c',textwrap.dedent('''
        import time
        from model_training_coordinator import training_slot
        with training_slot() as ok:
            print('LOCKED' if ok else 'FAILED', flush=True)
            time.sleep(2)
    ''')],stdout=subprocess.PIPE,text=True,env=env)
    assert holder.stdout.readline().strip()=='LOCKED'
    probe=subprocess.check_output([sys.executable,'-c',textwrap.dedent('''
        from model_training_coordinator import training_slot
        with training_slot() as ok: print(ok)
    ''')],text=True,env=env).strip()
    holder.wait(timeout=5)
    if holder.stdout: holder.stdout.close()
    assert probe=='False'


def test_trade_learning_never_substitutes_planned_entry(monkeypatch,tmp_path):
    import trade_outcome_tracker as t
    monkeypatch.setattr(t,'DB_PATH',tmp_path/'o.db')
    monkeypatch.setattr(t,'_market_price_at_signal',lambda signal:110.0)
    saved=[]
    class Store:
        def save(self,payload): saved.append(payload); return 'cloud-1'
    import cloud_learning_store
    monkeypatch.setattr(cloud_learning_store,'CloudLearningStore',lambda:Store())
    signal={'fingerprint':'fp','symbol':'BTCUSDT','direction':'LONG','entryPrice':100,'signal_created_at':datetime.now(timezone.utc).isoformat()}
    assert t.persist_trade_signal(signal)=='cloud-1'
    assert saved[0]['entry_price']==110.0
    assert saved[0]['market_price_at_signal']==110.0
    assert saved[0]['metadata']['planned_entry_price']==100.0


def test_learning_max_feedback_is_connected():
    text=Path('trade_outcome_tracker.py').read_text()
    assert 'learning_max2 import update_result' in text
    assert '_learning_max_update_result(original_fp' in text


def test_alpha_outcomes_use_historical_candles_not_simple_price():
    text=Path('outcome_tracker.py').read_text()
    assert 'historical_price_at' in text
    assert 'api.coingecko.com/api/v3/simple/price' not in text


def test_shadow_outcomes_use_historical_candles_and_strict_side():
    text=Path('shadow_signals.py').read_text()
    assert 'historical_price_at' in text
    assert 'def _side(direction)' in text
    assert "fill=target; fill_dt=cend" in text


def test_v45_migration_has_dedupe_and_compare_promote():
    text=Path('migrations/SUPABASE_V45_INTEGRITY.sql').read_text()
    assert 'row_number()' in text
    assert 'learning_observation_upsert_v45' in text
    assert 'adaptive_model_compare_promote_v45' in text
    assert 'p_expected_champion' in text
    assert 'pg_advisory_xact_lock' in text


def test_adaptive_runtime_uses_v45_compare_promote():
    text=Path('adaptive_model_manager.py').read_text()
    assert 'adaptive_model_compare_promote_v45' in text
    assert 'p_expected_champion' in text


def test_weight_change_invalidates_calibration(monkeypatch,tmp_path):
    import model_control as mc
    monkeypatch.setattr(mc,'DB_PATH',tmp_path/'model.db')
    mc.initialize()
    mc.set_weight_control('trend',mode='manual',base_weight=1.2,updated_by='test')
    assert mc.calibration_valid() is False


def test_training_marks_calibration_valid_api_exists():
    assert 'mark_calibration_valid(True' in Path('learning_engine_v14.py').read_text()


def test_paper_ledger_repair_is_throttled():
    text=Path('paper_trading.py').read_text()
    assert 'PAPER_LEDGER_REPAIR_INTERVAL_SECONDS' in text
    assert '_LAST_LEDGER_REPAIR_AT' in text
    assert '_int("PAPER_STATS_MAX_TRADES", 10000)' in text


def test_telegram_polling_marks_update_only_after_processing():
    text=Path('telegram_command_bot.py').read_text()
    a=text.index('process_update(update)')
    b=text.index('_mark_update_processed(update_id)',a)
    assert b>a
    assert '_durable_next_offset()' in text


def test_html_splitter_balances_tags():
    from telegram_ui.client import split_telegram_message
    text='<b>'+('x'*150)+'</b>'
    parts=split_telegram_message(text,limit=60)
    assert len(parts)>1
    assert all(p.startswith('<b>') and p.endswith('</b>') for p in parts)
    assert all(len(p)<=60 for p in parts)


def test_automation_guard_persists_state():
    text=Path('background_services.py').read_text()
    assert 'save_service_state(name, True' in text
    assert 'save_service_state(name, False' in text


def test_strategy_repo_update_returns_success():
    text=Path('strategies/repository.py').read_text()
    assert 'def update_setup' in text and 'return True' in text[text.index('def update_setup'):text.index('def pending_notifications')]
