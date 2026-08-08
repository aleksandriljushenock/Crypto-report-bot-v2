from core import runtime_state


def test_runtime_state_lifecycle():
    runtime_state.start('scanner', owner='manual_trade_scan', phase='universe', processed=0, total=10)
    state = runtime_state.get('scanner')
    assert state['running'] is True
    assert state['owner'] == 'manual_trade_scan'
    assert state['startedAt']

    runtime_state.update('scanner', phase='analysis', processed=4)
    state = runtime_state.get('scanner')
    assert state['phase'] == 'analysis'
    assert state['processed'] == 4
    assert state['total'] == 10

    runtime_state.finish('scanner')
    state = runtime_state.get('scanner')
    assert state['running'] is False
    assert state['phase'] == 'idle'
    assert state['processed'] == 0


def test_heavy_task_lifecycle():
    runtime_state.start('heavy_task', name='ai-optimizer')
    assert runtime_state.get('heavy_task')['name'] == 'ai-optimizer'
    runtime_state.finish('heavy_task')
    assert runtime_state.get('heavy_task')['running'] is False
    assert runtime_state.get('heavy_task')['name'] is None
