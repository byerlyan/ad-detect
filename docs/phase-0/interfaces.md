# Component contracts

These are design contracts, not frozen Python APIs. Phase 1 should encode them
as immutable dataclasses/Pydantic boundary schemas and `typing.Protocol` ports.
All timestamps are monotonic nanoseconds within a capture session plus a UTC
wall-clock timestamp for human correlation. Domain logic uses the monotonic
value.

## Core value objects

```python
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol, Sequence
from uuid import UUID


class Classification(StrEnum):
    PROGRAM = "program"
    COMMERCIAL = "commercial"
    UNKNOWN = "unknown"


class RecommendedAction(StrEnum):
    NONE = "none"
    MUTE = "mute"
    RESTORE = "restore"


@dataclass(frozen=True, slots=True)
class MediaStamp:
    session_id: UUID
    sequence: int
    pts_ns: int
    captured_monotonic_ns: int
    captured_utc: str
    discontinuity: bool = False


@dataclass(frozen=True, slots=True)
class AudioChunk:
    stamp: MediaStamp
    sample_rate_hz: int
    channels: int
    sample_format: str
    pcm: memoryview


@dataclass(frozen=True, slots=True)
class VideoFrame:
    stamp: MediaStamp
    width: int
    height: int
    pixel_format: str
    pixels: memoryview


@dataclass(frozen=True, slots=True)
class CaptureHealth:
    at_ns: int
    video_valid: bool
    audio_valid: bool
    av_skew_ms: float | None
    black_frame: bool
    frozen_frame: bool
    stale_ms: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FeatureWindow:
    schema_version: str
    start_ns: int
    end_ns: int
    values: Mapping[str, float]
    missing: frozenset[str]


@dataclass(frozen=True, slots=True)
class DetectorEvidence:
    detector_id: str
    detector_version: str
    window_start_ns: int
    window_end_ns: int
    probabilities: Mapping[Classification, float]
    valid: bool
    latency_ms: float
    explanation: tuple[str, ...]
    metadata: Mapping[str, str | int | float | bool]


@dataclass(frozen=True, slots=True)
class FingerprintMatch:
    ad_id: UUID
    confidence: float
    aligned_hashes: int
    query_hashes: int
    playback_position_ms: int
    offset_dispersion_ms: float
    runner_up_margin: float
    reference_version: str


@dataclass(frozen=True, slots=True)
class Decision:
    classification: Classification
    confidence: float
    break_active: bool
    action: RecommendedAction
    reasons: tuple[str, ...]
    evidence_versions: Mapping[str, str]
    expires_ns: int
```

Invariants:

- Probabilities are finite, in `[0, 1]`, and sum to 1 within tolerance.
- An invalid detector result cannot assign positive commercial evidence.
- `CaptureHealth` that is stale or invalid forces the fused observation to
  `UNKNOWN` and makes `MUTE` ineligible.
- Decisions expire; stale decisions cannot drive intervention.
- Payload buffers are read-only after publication.

## Ports

```python
class CaptureSource(Protocol):
    async def discover(self) -> Sequence["CaptureDevice"]: ...
    async def probe(self, config: "CaptureConfig") -> "CaptureProbe": ...
    async def start(self, config: "CaptureConfig", sink: "MediaSink") -> None: ...
    async def stop(self) -> None: ...


class FeatureExtractor(Protocol):
    @property
    def schema_version(self) -> str: ...
    async def extract(self, window: "SynchronizedWindow") -> FeatureWindow: ...


class Detector(Protocol):
    @property
    def detector_id(self) -> str: ...
    async def infer(self, features: FeatureWindow) -> DetectorEvidence: ...


class FingerprintIndex(Protocol):
    async def query(self, audio: AudioChunk) -> Sequence[FingerprintMatch]: ...
    async def enroll(self, sample: "TrustedAdSample") -> UUID: ...
    async def remove(self, ad_id: UUID) -> None: ...
    async def find_duplicates(self, sample: "TrustedAdSample") -> Sequence[FingerprintMatch]: ...


class TemporalClassifier(Protocol):
    def update(
        self,
        evidence: Sequence[DetectorEvidence],
        matches: Sequence[FingerprintMatch],
        health: CaptureHealth,
    ) -> "TemporalState": ...
    def reset(self, reason: str) -> None: ...


class DecisionPolicy(Protocol):
    def decide(
        self,
        temporal: "TemporalState",
        mode: "OperatingMode",
        intervention: "InterventionSnapshot",
        override: "ManualOverride | None",
    ) -> Decision: ...


class InterventionPort(Protocol):
    async def discover(self) -> Sequence["PlaybackDevice"]: ...
    async def capabilities(self) -> "InterventionCapabilities": ...
    async def connect(self, device_id: str) -> None: ...
    async def snapshot(self) -> "InterventionSnapshot": ...
    async def request(self, desired: "DesiredAudioState", generation: int) -> "ActionResult": ...
    async def disconnect(self) -> None: ...


class EventRepository(Protocol):
    async def append(self, event: "DomainEvent") -> None: ...
    async def timeline(self, query: "TimelineQuery") -> Sequence["DomainEvent"]: ...


class ArtifactStore(Protocol):
    async def retain(self, request: "RetentionRequest") -> "ArtifactManifest": ...
    async def open(self, artifact_id: UUID) -> "ArtifactReader": ...
    async def apply_retention(self, policy: "RetentionPolicy") -> "RetentionReport": ...


class ModelRegistry(Protocol):
    async def register(self, bundle: "ValidatedModelBundle") -> str: ...
    async def activate(self, model_id: str, actor: str) -> None: ...
    async def rollback(self, model_id: str, actor: str) -> None: ...
```

## Queue semantics

| Channel | Capacity policy | On overflow | Rationale |
| --- | --- | --- | --- |
| Capture to health | Small, latest-value | Replace old | Health must describe now |
| Capture to live features | Time-bounded | Drop oldest whole window; record gap | Latency is safer than backlog |
| Capture to rolling buffer | Duration-bounded | Evict oldest unlabeled data | Fixed memory/storage |
| Features to detectors | Per-detector bounded queue | Skip oldest; emit missed-inference event | One detector cannot block peers |
| Evidence to temporal fusion | Latest per detector/window | Mark missing at deadline | Deterministic fusion cadence |
| Decisions to intervention | Conflated desired state | Keep newest generation | Idempotent actuator |
| Events to persistence | Durable bounded spool | Degrade UI detail, never block capture | Control safety takes precedence |

Every overflow increments a metric and creates a diagnostic event. Silent loss is
not allowed.

## Detector contract details

Detectors must not encode action policy. A detector may say that a window has a
0.82 commercial probability; it may not request mute. Each result includes:

- exact input interval and feature-schema version;
- model/detector version and calibration version;
- all three class probabilities, including `UNKNOWN`;
- validity and missing-input information;
- measured inference latency;
- concise, machine-storable explanation contributions.

Optional detectors load through explicit configuration. Failure to import or
load one reduces available evidence and is visible in health; it does not stop
capture or turn into commercial evidence.

## Setup state machine

Setup is a persisted state machine, not a page sequence coupled to browser
history:

```text
WELCOME -> VIDEO_PROBED -> AUDIO_PROBED -> APPLE_TV_PAIRED
        -> MODE_SELECTED -> CALIBRATED? -> READINESS_CHECKED
        -> SHADOW_VALIDATED -> AUTO_MUTE_CONSENTED -> COMPLETE
```

Optional steps may be skipped with a recorded reason. Each completed step stores
the configuration revision and probe result. Hardware changes invalidate only
dependent steps. Automatic mute cannot become enabled without explicit consent
after a successful shadow validation and intervention safety check.

## Persistence boundaries

SQLite is the source of truth for configuration revisions, sessions, events,
labels, fingerprints metadata, model metadata, and audits. Large media and array
artifacts live in content-addressed files; SQLite stores hashes, paths, sizes,
capture intervals, and provenance.

Writes use WAL mode and short transactions. Raw frames never become database
blobs. Repository methods are the only component allowed to know table layout.

