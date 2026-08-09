"""Typed exchange capability result.

Unknown/unavailable market metrics must not be confused with numeric zero.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class CapabilityValue:
    status: str  # supported | unavailable | unsupported
    value: Any = None
    provider: str | None = None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.status == "supported"
