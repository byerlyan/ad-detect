"""Immutable records exchanged between application components."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from types import MappingProxyType


class Classification(StrEnum):
    PROGRAM = "program"
    COMMERCIAL = "commercial"
    UNKNOWN = "unknown"


class RecommendedAction(StrEnum):
    NONE = "none"
    MUTE = "mute"
    RESTORE = "restore"


class OperatingMode(StrEnum):
    DETECTION_ONLY = "detection_only"
    SHADOW = "shadow"
    AUTOMATIC = "automatic"


class TemporalStrategy(StrEnum):
    HYSTERESIS = "hysteresis"
    SLIDING_WINDOW = "sliding_window"
    HMM = "hmm"


def _validate_probabilities(values: Mapping[Classification, float]) -> None:
    expected = set(Classification)
    if set(values) != expected:
        missing = expected - set(values)
        extra = set(values) - expected
        raise ValueError(f"probabilities require all classes; missing={missing}, extra={extra}")
    if any(not isfinite(value) or value < 0.0 or value > 1.0 for value in values.values()):
        raise ValueError("probabilities must be finite and in [0, 1]")
    if abs(sum(values.values()) - 1.0) > 1e-6:
        raise ValueError("probabilities must sum to 1")


def probabilities(
    program: float,
    commercial: float,
    unknown: float,
) -> Mapping[Classification, float]:
    result = {
        Classification.PROGRAM: float(program),
        Classification.COMMERCIAL: float(commercial),
        Classification.UNKNOWN: float(unknown),
    }
    _validate_probabilities(result)
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True)
class CaptureHealth:
    at_ns: int
    video_valid: bool
    audio_valid: bool
    stale_ms: float
    av_skew_ms: float | None = None
    black_frame: bool = False
    frozen_frame: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return (
            self.video_valid
            and self.audio_valid
            and not self.black_frame
            and not self.frozen_frame
            and self.stale_ms >= 0
            and not self.reasons
        )


@dataclass(frozen=True, slots=True)
class DetectorEvidence:
    detector_id: str
    detector_version: str
    window_start_ns: int
    window_end_ns: int
    probabilities: Mapping[Classification, float]
    valid: bool
    latency_ms: float = 0.0
    explanation: tuple[str, ...] = ()
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.window_end_ns < self.window_start_ns:
            raise ValueError("evidence window end precedes start")
        _validate_probabilities(self.probabilities)
        if self.latency_ms < 0 or not isfinite(self.latency_ms):
            raise ValueError("latency must be a finite non-negative value")
        if not self.valid and self.probabilities[Classification.COMMERCIAL] > 0.0:
            raise ValueError("invalid evidence cannot contribute commercial probability")


@dataclass(frozen=True, slots=True)
class TemporalState:
    at_ns: int
    classification: Classification
    confidence: float
    probabilities: Mapping[Classification, float]
    commercial_ticks: int
    program_ticks: int
    reasons: tuple[str, ...] = ()
    evidence_versions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_probabilities(self.probabilities)
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class Decision:
    at_ns: int
    classification: Classification
    confidence: float
    break_active: bool
    action: RecommendedAction
    reasons: tuple[str, ...]
    expires_ns: int
    evidence_versions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.expires_ns <= self.at_ns:
            raise ValueError("decision must expire after it is created")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")

    def is_expired(self, now_ns: int) -> bool:
        return now_ns >= self.expires_ns
