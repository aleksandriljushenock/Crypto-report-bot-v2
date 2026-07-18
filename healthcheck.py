from __future__ import annotations

import importlib
import sys
from pathlib import Path

from core.settings import settings

CRITICAL_MODULES = [
    "config",
    "telegram_command_bot",
    "background_services",
    "trade_engine",
    "early_discovery_pipeline",
    "listing_pipeline",
    "capital_flow_engine",
    "news_engine",
    "ai_score_engine",
    "ai_intelligence",
]


def main() -> int:
    failures = []
    print("Crypto Intelligence Platform v14 healthcheck")
    print(f"Base directory: {settings.base_dir}")
    for module_name in CRITICAL_MODULES:
        try:
            importlib.import_module(module_name)
            print(f"[OK] {module_name}")
        except Exception as exc:
            failures.append((module_name, exc))
            print(f"[FAIL] {module_name}: {exc}")
    writable = [settings.data_dir, settings.log_dir]
    for path in writable:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".healthcheck"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            print(f"[OK] writable: {path}")
        except Exception as exc:
            failures.append((str(path), exc))
            print(f"[FAIL] writable: {path}: {exc}")
    if failures:
        print(f"Healthcheck failed: {len(failures)} error(s)")
        return 1
    print("Healthcheck passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
