"""Immutable audio-fingerprint records and trusted enrollment input."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from .media import AudioChunk


class FingerprintStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class AudioLandmark:
    hash_value: int
    anchor_ms: int

    def __post_init__(self) -> None:
        if self.hash_value < 0 or self.anchor_ms < 0:
            raise ValueError("fingerprint landmark values must be non-negative")


@dataclass(frozen=True, slots=True)
class TrustedAdSample:
    name: str
    label_revision_id: str
    session_id: str
    start_ns: int
    end_ns: int
    audio: tuple[AudioChunk, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("fingerprint name is required")
        if self.end_ns <= self.start_ns:
            raise ValueError("trusted sample end must follow start")
        if not self.audio:
            raise ValueError("trusted sample requires audio")


@dataclass(frozen=True, slots=True)
class FingerprintReference:
    ad_id: UUID
    name: str
    label_revision_id: str
    session_id: str
    start_ns: int
    end_ns: int
    duration_ms: int
    fingerprint_version: str
    landmark_count: int
    status: FingerprintStatus
    created_at_ns: int


@dataclass(frozen=True, slots=True)
class FingerprintHashHit:
    hash_value: int
    ad_id: UUID
    anchor_ms: int


@dataclass(frozen=True, slots=True)
class FingerprintMatch:
    ad_id: UUID
    name: str
    confidence: float
    aligned_hashes: int
    query_hashes: int
    playback_position_ms: int
    offset_dispersion_ms: float
    runner_up_margin: float
    reference_version: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("fingerprint confidence must be in [0, 1]")
        if self.aligned_hashes < 0 or self.query_hashes < 0:
            raise ValueError("fingerprint hash counts must be non-negative")
        if self.aligned_hashes > self.query_hashes:
            raise ValueError("aligned hashes cannot exceed query hashes")
        if self.playback_position_ms < 0 or self.offset_dispersion_ms < 0:
            raise ValueError("fingerprint positions and dispersion must be non-negative")
        if self.runner_up_margin < 0:
            raise ValueError("fingerprint runner-up margin must be non-negative")
