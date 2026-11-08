"""Immutable setup-workflow records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SetupStep(StrEnum):
    WELCOME = "welcome"
    VIDEO = "video"
    AUDIO = "audio"
    APPLE_TV = "apple_tv"
    MODE = "mode"
    CALIBRATION = "calibration"
    READINESS = "readiness"
    SHADOW = "shadow"
    AUTO_MUTE_CONSENT = "auto_mute_consent"


SETUP_STEP_ORDER = tuple(SetupStep)


class SetupStepStatus(StrEnum):
    COMPLETE = "complete"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class SetupStepResult:
    step: SetupStep
    status: SetupStepStatus
    config_digest: str
    payload: Mapping[str, Any]
    actor: str
    completed_at_ns: int


@dataclass(frozen=True, slots=True)
class SetupSnapshot:
    current_step: SetupStep | None
    completed: tuple[SetupStepResult, ...]
    ready_for_shadow: bool
    automatic_consent_available: bool

