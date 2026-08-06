from __future__ import annotations

import gc
import os


def rss_mb() -> float:
    """Return current RSS, not process lifetime peak RSS."""
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


def pressure() -> dict:
    soft = float(os.getenv('MEMORY_SOFT_LIMIT_MB', '390'))
    hard = float(os.getenv('MEMORY_HARD_LIMIT_MB', '500'))
    current = rss_mb()
    return {
        'rssMb': current,
        'softLimitMb': soft,
        'hardLimitMb': hard,
        'high': current >= soft,
        'critical': current >= hard,
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
