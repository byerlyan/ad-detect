"""Idempotent in-memory actuator for tests and shadow demonstrations."""

from __future__ import annotations

import asyncio

from ad_detect.ports import (
    ActionResult,
    DesiredAudioState,
    InterventionCapabilities,
    PlaybackDevice,
)


class SimulatedIntervention:
    def __init__(self) -> None:
        self._state = DesiredAudioState.RESTORED
        self._generation = 0
        self._lock = asyncio.Lock()
        self.history: list[ActionResult] = []

    @property
    def state(self) -> DesiredAudioState:
        return self._state

    @property
    def audio_state(self) -> DesiredAudioState:
        return self._state

    async def discover(self) -> tuple[PlaybackDevice, ...]:
        return (
            PlaybackDevice(
                identifier="simulated",
                name="Simulated playback device",
                address="local",
                protocols=("simulated",),
            ),
        )

    async def connect(self, device_id: str) -> None:
        if device_id != "simulated":
            raise ConnectionError(f"simulated device {device_id!r} not found")

    def capabilities(self) -> InterventionCapabilities:
        return InterventionCapabilities(
            connected=True,
            volume_observable=True,
            absolute_volume=True,
            step_volume_up=True,
            step_volume_down=True,
            safe_automatic_mute=True,
        )

    async def request(self, desired: DesiredAudioState, generation: int) -> ActionResult:
        async with self._lock:
            if generation < self._generation:
                result = ActionResult(
                    generation=generation,
                    desired=desired,
                    changed=False,
                    acknowledged=False,
                    explanation="stale action generation ignored",
                )
            else:
                self._generation = generation
                changed = desired is not self._state
                self._state = desired
                result = ActionResult(
                    generation=generation,
                    desired=desired,
                    changed=changed,
                    acknowledged=True,
                    explanation=(
                        "simulated state changed" if changed else "desired state already applied"
                    ),
                )
            self.history.append(result)
            return result

    async def restore_for_shutdown(self) -> ActionResult:
        return await self.request(DesiredAudioState.RESTORED, self._generation + 1)

    async def disconnect(self) -> None:
        await self.restore_for_shutdown()
