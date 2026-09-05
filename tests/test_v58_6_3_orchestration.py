from pathlib import Path
import threading
import time


def test_version_5863():
    assert Path('VERSION').read_text().strip() == '58.6.3'


def test_heavy_queue_prefers_execution_when_slot_releases():
    from core.heavy_task_coordinator import heavy_slot
    order=[]
    ready=threading.Event()
    release=threading.Event()
    def holder():
        with heavy_slot('self-learning-engine', wait_seconds=2) as ok:
            assert ok
            ready.set(); release.wait(1)
    def waiter(name):
        with heavy_slot(name, wait_seconds=2) as ok:
            if ok: order.append(name)
    h=threading.Thread(target=holder); h.start(); assert ready.wait(1)
    low=threading.Thread(target=waiter,args=('profit-profile-rebuild',)); low.start()
    time.sleep(.03)
    high=threading.Thread(target=waiter,args=('execution-v57-model-trainer',)); high.start()
    time.sleep(.03); release.set()
    h.join(2); low.join(2); high.join(2)
    assert order[0] == 'execution-v57-model-trainer'


def test_execution_worker_owns_distributed_lease_and_reports_progress():
    text=Path('execution_auto_worker.py').read_text()
    assert "training_slot(owner='execution-auto-v58.6.3')" in text
    assert "_progress('backfill'" in text
    assert "_progress('train')" in text
    assert "_progress('diagnose')" in text


def test_network_trackers_do_not_hold_training_queue():
    text=Path('background_services.py').read_text()
    assert "_guarded('trade-outcome-tracker', self._run_trade_outcomes, lock_kind='io')" in text
    assert "_guarded('outcome-tracker', self._run_outcomes, lock_kind='io')" in text
    assert "_guarded('execution-v57-model-trainer', self._run_execution_model_v57)" in text


def test_invalid_symbol_errors_are_negative_cached():
    import trade_market_client as t
    assert t._looks_like_unsupported_symbol(RuntimeError('Bybit error 10001: params error: Symbol Is Invalid'))
    assert t._looks_like_unsupported_symbol(RuntimeError('KuCoin ticker not found: GHO'))
    assert not t._looks_like_unsupported_symbol(RuntimeError('timeout while reading response'))


def test_release_builder_targets_new_release():
    text=Path('scripts/build_release.py').read_text()
    assert 'Crypto-report-bot-v58.6.3-orchestrated.zip' in text

def test_trade_symbol_canonicalization_for_legacy_base_symbols():
    import trade_market_client as t
    assert t.normalize_trade_symbol('GHO') == 'GHOUSDT'
    assert t.normalize_trade_symbol('btc/usdt') == 'BTCUSDT'
    assert t.normalize_trade_symbol('ETH-USDT') == 'ETHUSDT'
