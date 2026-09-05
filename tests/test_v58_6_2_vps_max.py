from pathlib import Path


def test_memory_guard_autoscales_legacy_limits(monkeypatch):
    import memory_guard as m
    monkeypatch.setenv('MEMORY_AUTO_SCALE','true')
    monkeypatch.setenv('MEMORY_SOFT_LIMIT_MB','340')
    monkeypatch.setenv('MEMORY_HARD_LIMIT_MB','470')
    monkeypatch.setattr(m,'system_memory',lambda:{'effectiveTotalMb':6144.0,'effectiveAvailableMb':5000.0,'hostTotalMb':7895.0,'hostAvailableMb':6800.0,'cgroupLimitMb':6144.0,'cgroupCurrentMb':1100.0})
    monkeypatch.setattr(m,'rss_mb',lambda:768.0)
    p=m.pressure()
    assert p['autoScaled'] is True
    assert p['hardLimitMb'] > 5000
    assert p['critical'] is False


def test_memory_guard_uses_available_memory(monkeypatch):
    import memory_guard as m
    monkeypatch.setenv('MEMORY_AUTO_SCALE','false')
    monkeypatch.setenv('MEMORY_SOFT_LIMIT_MB','4600')
    monkeypatch.setenv('MEMORY_HARD_LIMIT_MB','5600')
    monkeypatch.setenv('MEMORY_MIN_AVAILABLE_HARD_MB','1100')
    monkeypatch.setattr(m,'system_memory',lambda:{'effectiveTotalMb':6144.0,'effectiveAvailableMb':900.0,'hostTotalMb':7895.0,'hostAvailableMb':900.0,'cgroupLimitMb':6144.0,'cgroupCurrentMb':5244.0})
    monkeypatch.setattr(m,'rss_mb',lambda:800.0)
    assert m.pressure()['critical'] is True


def test_vps_compose_has_resource_ceiling():
    text=Path('docker-compose.vps.yml').read_text()
    assert 'cpus: "4.0"' in text
    assert 'mem_limit: 6g' in text
    assert 'VPS_OMP_NUM_THREADS:-4' in text


def test_release_builder_includes_vps_dockerfile():
    text=Path('scripts/build_release.py').read_text()
    assert 'Dockerfile.vps' in text
    assert Path('Dockerfile.vps').exists()


def test_autonomous_worker_isolated_and_diagnostic_versioned():
    text=Path('execution_auto_worker.py').read_text()
    assert 'execution_v58_6_2_latest_diagnostic.json' in text
    assert "train(trigger='scheduled-auto-subprocess')" in text


def test_execution_capacity_defaults_are_vps_max(monkeypatch):
    import execution_model_v57 as m
    monkeypatch.delenv('EXECUTION_ML_N_JOBS', raising=False)
    extra=m._family('extra',123)
    assert extra.n_jobs == 4
    assert extra.n_estimators == 600
