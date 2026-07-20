from __future__ import annotations

import gc
import os
import resource


def rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB, macOS bytes. Render is Linux.
    return round(value / 1024.0, 1)


def pressure() -> dict:
    limit = float(os.getenv("MEMORY_SOFT_LIMIT_MB", "430"))
    current = rss_mb()
    return {"rssMb": current, "softLimitMb": limit, "high": current >= limit}


def cleanup() -> dict:
    collected = gc.collect()
    data = pressure()
    data["objectsCollected"] = collected
    return data
