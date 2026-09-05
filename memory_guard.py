from __future__ import annotations

import gc
import os
from typing import Optional


def _read_meminfo_mb(key: str) -> Optional[float]:
    try:
        with open('/proc/meminfo', 'r', encoding='utf-8') as handle:
            for line in handle:
                if line.startswith(key + ':'):
                    return float(line.split()[1]) / 1024.0
    except Exception:
        return None
    return None


def _read_number(path: str) -> Optional[float]:
    try:
        raw = open(path, 'r', encoding='utf-8').read().strip()
        if not raw or raw == 'max':
            return None
        value = float(raw)
        if value <= 0 or value > 2**60:
            return None
        return value
    except Exception:
        return None


def _cgroup_memory() -> tuple[Optional[float], Optional[float]]:
    """Return (limit_mb, current_mb) for cgroup v2/v1 when bounded."""
    limit = _read_number('/sys/fs/cgroup/memory.max')
    current = _read_number('/sys/fs/cgroup/memory.current')
    if limit is None:
        limit = _read_number('/sys/fs/cgroup/memory/memory.limit_in_bytes')
        current = _read_number('/sys/fs/cgroup/memory/memory.usage_in_bytes')
    if limit is None:
        return None, None
    return limit / (1024.0 * 1024.0), (current / (1024.0 * 1024.0) if current is not None else None)


def rss_mb() -> float:
    """Return current process RSS, not process lifetime peak RSS."""
    try:
        with open('/proc/self/status', 'r', encoding='utf-8') as handle:
            for line in handle:
                if line.startswith('VmRSS:'):
                    return round(float(line.split()[1]) / 1024.0, 1)
    except Exception:
        pass
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(value / 1024.0, 1)
    except Exception:
        return 0.0


def system_memory() -> dict:
    host_total = _read_meminfo_mb('MemTotal') or 0.0
    host_available = _read_meminfo_mb('MemAvailable') or 0.0
    cgroup_limit, cgroup_current = _cgroup_memory()
    effective_total = host_total
    if cgroup_limit and cgroup_limit > 0:
        effective_total = min(host_total or cgroup_limit, cgroup_limit)
    effective_available = host_available
    if cgroup_limit and cgroup_current is not None:
        cgroup_available = max(0.0, cgroup_limit - cgroup_current)
        effective_available = min(host_available or cgroup_available, cgroup_available)
    return {
        'hostTotalMb': round(host_total, 1),
        'hostAvailableMb': round(host_available, 1),
        'cgroupLimitMb': None if cgroup_limit is None else round(cgroup_limit, 1),
        'cgroupCurrentMb': None if cgroup_current is None else round(cgroup_current, 1),
        'effectiveTotalMb': round(effective_total, 1),
        'effectiveAvailableMb': round(effective_available, 1),
    }


def pressure() -> dict:
    # Defaults target the user's 4 vCPU / 7.71 GiB VPS. Environment variables
    # remain authoritative so smaller deployments can override the profile.
    current = rss_mb()
    sysmem = system_memory()
    effective_total = float(sysmem.get('effectiveTotalMb') or 0.0)
    soft = float(os.getenv('MEMORY_SOFT_LIMIT_MB', '4600'))
    hard = float(os.getenv('MEMORY_HARD_LIMIT_MB', '5600'))
    reserve_soft = float(os.getenv('MEMORY_MIN_AVAILABLE_SOFT_MB', '1800'))
    reserve_hard = float(os.getenv('MEMORY_MIN_AVAILABLE_HARD_MB', '1100'))
    auto_scale = os.getenv('MEMORY_AUTO_SCALE', 'true').strip().lower() not in {'0','false','no','off'}
    # Migrate legacy 512-MB/Render settings automatically on a large VPS.
    # This prevents an old .env with 340/470 MB from disabling services on an 8-GB host.
    if auto_scale and effective_total >= 6000 and hard <= 1024:
        soft = min(4600.0, effective_total * 0.72)
        hard = min(5600.0, effective_total * 0.88)
        reserve_soft = max(1600.0, effective_total * 0.22)
        reserve_hard = max(900.0, effective_total * 0.14)
    available = float(sysmem.get('effectiveAvailableMb') or 0.0)
    high = current >= soft or (available > 0 and available <= reserve_soft)
    critical = current >= hard or (available > 0 and available <= reserve_hard)
    return {
        'rssMb': current,
        'softLimitMb': soft,
        'hardLimitMb': hard,
        'minAvailableSoftMb': reserve_soft,
        'minAvailableHardMb': reserve_hard,
        'autoScaled': bool(auto_scale and effective_total >= 6000 and float(os.getenv('MEMORY_HARD_LIMIT_MB', '5600')) <= 1024),
        'high': high,
        'critical': critical,
        **sysmem,
    }


def cleanup(trim: bool = True) -> dict:
    collected = gc.collect()
    if trim:
        try:
            import ctypes
            ctypes.CDLL('libc.so.6').malloc_trim(0)
        except Exception:
            pass
    data = pressure()
    data['objectsCollected'] = collected
    return data
