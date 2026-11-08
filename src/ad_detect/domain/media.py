"""Timestamped media and hardware discovery records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CaptureDeviceKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


@dataclass(frozen=True, slots=True)
class CaptureDevice:
    kind: CaptureDeviceKind
    index: int
    name: str

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.index}:{self.name}"


@dataclass(frozen=True, slots=True)
class MediaStamp:
    session_id: str
    sequence: int
    pts_ns: int
    captured_monotonic_ns: int
    discontinuity: bool = False


@dataclass(frozen=True, slots=True)
class AudioChunk:
    stamp: MediaStamp
    sample_rate_hz: int
    channels: int
    sample_format: str
    pcm: bytes


@dataclass(frozen=True, slots=True)
class VideoFrame:
    stamp: MediaStamp
    width: int
    height: int
    pixel_format: str
    pixels: bytes


@dataclass(frozen=True, slots=True)
class CaptureProbe:
    success: bool
    command: tuple[str, ...]
    duration_seconds: float
    negotiated_video: str | None
    negotiated_audio: str | None
    stderr_tail: tuple[str, ...]
    reason: str | None = None
