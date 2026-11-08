"""Versioned synchronized-window and feature records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType

from .media import AudioChunk, VideoFrame
from .models import CaptureHealth


@dataclass(frozen=True, slots=True)
class SynchronizedWindow:
    session_id: str
    start_ns: int
    end_ns: int
    audio: tuple[AudioChunk, ...]
    video: tuple[VideoFrame, ...]
    health: CaptureHealth

    def __post_init__(self) -> None:
        if self.end_ns <= self.start_ns:
            raise ValueError("synchronized window end must follow start")


@dataclass(frozen=True, slots=True)
class FeatureWindow:
    schema_version: str
    session_id: str
    start_ns: int
    end_ns: int
    values: Mapping[str, float]
    missing: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.end_ns <= self.start_ns:
            raise ValueError("feature window end must follow start")
        if any(not isfinite(value) for value in self.values.values()):
            raise ValueError("feature values must be finite")


def feature_values(values: Mapping[str, float]) -> Mapping[str, float]:
    normalized = {str(key): float(value) for key, value in values.items()}
    if any(not isfinite(value) for value in normalized.values()):
        raise ValueError("feature values must be finite")
    return MappingProxyType(normalized)
