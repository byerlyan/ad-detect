"""Playback intervention contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PlaybackDevice:
    identifier: str
    name: str
    address: str
    protocols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterventionCapabilities:
    connected: bool
    volume_observable: bool
    absolute_volume: bool
    step_volume_up: bool
    step_volume_down: bool
    safe_automatic_mute: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PairingSession:
    token: str
    device_id: str
    protocol: str
    device_provides_pin: bool


class DesiredAudioState(StrEnum):
    MUTED = "muted"
    RESTORED = "restored"


@dataclass(frozen=True, slots=True)
class ActionResult:
    generation: int
    desired: DesiredAudioState
    changed: bool
    acknowledged: bool
    explanation: str


class InterventionPort(Protocol):
    @property
    def audio_state(self) -> DesiredAudioState: ...

    async def discover(self) -> tuple[PlaybackDevice, ...]: ...

    async def connect(self, device_id: str) -> None: ...

    def capabilities(self) -> InterventionCapabilities: ...

    async def request(self, desired: DesiredAudioState, generation: int) -> ActionResult: ...

    async def restore_for_shutdown(self) -> ActionResult: ...

    async def disconnect(self) -> None: ...
