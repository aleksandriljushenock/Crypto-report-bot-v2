#!/usr/bin/env python3
"""Compact diagnostics for the latest Execution Model bundle (v57.2+)."""
from __future__ import annotations
import json
from execution_model_v57 import diagnose

if __name__ == '__main__':
    print(json.dumps(diagnose(), ensure_ascii=False, indent=2, default=str))
