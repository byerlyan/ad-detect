"""Dependency-free domain value objects."""

from .features import FeatureWindow, SynchronizedWindow, feature_values
from .fingerprint import (
    AudioLandmark,
    FingerprintHashHit,
    FingerprintMatch,
    FingerprintReference,
    FingerprintStatus,
    TrustedAdSample,
)
from .labels import BoundaryKind, LabelRevision
from .media import (
    AudioChunk,
    CaptureDevice,
    CaptureDeviceKind,
    CaptureProbe,
    MediaStamp,
    VideoFrame,
)
from .models import (
    CaptureHealth,
    Classification,
    Decision,
    DetectorEvidence,
    OperatingMode,
    RecommendedAction,
    TemporalState,
    TemporalStrategy,
)
from .setup import (
    SETUP_STEP_ORDER,
    SetupSnapshot,
    SetupStep,
    SetupStepResult,
    SetupStepStatus,
)

__all__ = [
    "AudioChunk",
    "AudioLandmark",
    "BoundaryKind",
    "CaptureHealth",
    "CaptureDevice",
    "CaptureDeviceKind",
    "CaptureProbe",
    "FeatureWindow",
    "FingerprintMatch",
    "FingerprintHashHit",
    "FingerprintReference",
    "FingerprintStatus",
    "LabelRevision",
    "Classification",
    "Decision",
    "DetectorEvidence",
    "OperatingMode",
    "RecommendedAction",
    "TemporalStrategy",
    "TemporalState",
    "TrustedAdSample",
    "MediaStamp",
    "VideoFrame",
    "SynchronizedWindow",
    "SETUP_STEP_ORDER",
    "SetupSnapshot",
    "SetupStep",
    "SetupStepResult",
    "SetupStepStatus",
    "feature_values",
]
