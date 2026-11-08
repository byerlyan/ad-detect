"""Capability-driven Apple TV intervention using pyatv 0.18."""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any, cast
from uuid import uuid4

import pyatv
from pyatv.const import FeatureName, FeatureState, Protocol
from pyatv.interface import PairingHandler
from pyatv.storage.file_storage import FileStorage

from ad_detect.config.settings import InterventionSettings
from ad_detect.ports import (
    ActionResult,
    DesiredAudioState,
    InterventionCapabilities,
    PairingSession,
    PlaybackDevice,
)


class _ConnectionListener:
    def __init__(self, owner: AppleTVIntervention) -> None:
        self._owner = owner

    def connection_lost(self, exception: Exception) -> None:
        self._owner._mark_disconnected(f"connection lost: {exception}")

    def connection_closed(self) -> None:
        self._owner._mark_disconnected("connection closed")


class AppleTVIntervention:
    """Own one pyatv connection and restore only volume state it observed."""

    def __init__(
        self,
        settings: InterventionSettings,
        *,
        backend: Any = pyatv,
        storage: Any | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.settings = settings
        self._backend = backend
        self._loop = loop
        self._storage = storage
        self._atv: Any | None = None
        self._listener: _ConnectionListener | None = None
        self._device_id: str | None = None
        self._pairings: dict[str, PairingHandler | Any] = {}
        self._connected = False
        self._last_error: str | None = None
        self._generation = 0
        self._mute_owned = False
        self._pre_mute_volume: float | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def mute_owned(self) -> bool:
        return self._mute_owned

    @property
    def audio_state(self) -> DesiredAudioState:
        return DesiredAudioState.MUTED if self._mute_owned else DesiredAudioState.RESTORED

    def _event_loop(self) -> asyncio.AbstractEventLoop:
        return self._loop or asyncio.get_running_loop()

    async def _ensure_storage(self) -> Any:
        if self._storage is None:
            path = self.settings.credentials_path
            path.parent.mkdir(parents=True, exist_ok=True)
            self._storage = FileStorage(str(path), self._event_loop())
            await self._storage.load()
            self._secure_storage_file()
        return self._storage

    def _secure_storage_file(self) -> None:
        path = self.settings.credentials_path
        if path.exists():
            os.chmod(path, 0o600)

    async def _scan(self, identifier: str | None = None) -> list[Any]:
        storage = await self._ensure_storage()
        return cast(
            list[Any],
            await self._backend.scan(
                self._event_loop(),
                timeout=max(1, math.ceil(self.settings.discovery_timeout_seconds)),
                identifier=identifier,
                storage=storage,
            ),
        )

    async def discover(self) -> tuple[PlaybackDevice, ...]:
        configurations = await self._scan()
        devices: list[PlaybackDevice] = []
        for config in configurations:
            identifier = config.identifier
            if not identifier and config.all_identifiers:
                identifier = config.all_identifiers[0]
            if not identifier:
                continue
            devices.append(
                PlaybackDevice(
                    identifier=identifier,
                    name=config.name,
                    address=str(config.address),
                    protocols=tuple(
                        sorted(service.protocol.name.lower() for service in config.services)
                    ),
                )
            )
        return tuple(devices)

    async def begin_pairing(self, device_id: str, protocol: str) -> PairingSession:
        configurations = await self._scan(device_id)
        if not configurations:
            raise ConnectionError(f"Apple TV {device_id!r} not found")
        selected_protocol = next(
            (item for item in Protocol if item.name.lower() == protocol.lower()),
            None,
        )
        if selected_protocol is None:
            supported = ", ".join(item.name.lower() for item in Protocol)
            raise ValueError(f"unknown protocol {protocol!r}; expected one of {supported}")
        handler = await self._backend.pair(
            configurations[0],
            selected_protocol,
            self._event_loop(),
            storage=await self._ensure_storage(),
        )
        await handler.begin()
        token = uuid4().hex
        self._pairings[token] = handler
        return PairingSession(
            token=token,
            device_id=device_id,
            protocol=protocol.lower(),
            device_provides_pin=handler.device_provides_pin,
        )

    async def finish_pairing(self, token: str, pin: int | None) -> bool:
        handler = self._pairings.pop(token, None)
        if handler is None:
            raise ValueError("pairing session is missing or expired")
        try:
            if pin is not None:
                handler.pin(pin)
            await handler.finish()
            paired = bool(handler.has_paired)
            if paired:
                storage = await self._ensure_storage()
                await storage.save()
                self._secure_storage_file()
            return paired
        finally:
            await handler.close()

    async def cancel_pairing(self, token: str) -> None:
        handler = self._pairings.pop(token, None)
        if handler is not None:
            await handler.close()

    async def connect(self, device_id: str) -> None:
        if self._atv is not None:
            await self._close_transport()
        configurations = await self._scan(device_id)
        if not configurations:
            raise ConnectionError(f"Apple TV {device_id!r} not found")
        self._atv = await self._backend.connect(
            configurations[0],
            self._event_loop(),
            storage=await self._ensure_storage(),
        )
        self._listener = _ConnectionListener(self)
        self._atv.listener = self._listener
        self._connected = True
        self._device_id = device_id
        self._last_error = None
        # A reconnect while owning a mute is a recovery event: restore before
        # allowing any new intervention command.
        if self._mute_owned:
            await self._restore_owned_volume("restored after reconnect")

    async def reconnect(self, *, max_attempts: int = 3, base_delay_seconds: float = 0.5) -> None:
        if self._device_id is None:
            raise ConnectionError("no Apple TV has been selected")
        if max_attempts < 1 or base_delay_seconds < 0:
            raise ValueError("reconnect limits are invalid")
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                await self.connect(self._device_id)
                return
            except Exception as exc:  # pyatv raises protocol-specific connection errors
                last_error = exc
                self._mark_disconnected(f"reconnect failed: {exc}")
                if attempt + 1 < max_attempts:
                    await asyncio.sleep(base_delay_seconds * (2**attempt))
        raise ConnectionError(
            f"failed to reconnect to Apple TV after {max_attempts} attempts"
        ) from last_error

    def _mark_disconnected(self, reason: str) -> None:
        self._connected = False
        self._last_error = reason

    def _feature_available(self, feature: FeatureName) -> bool:
        return (
            self._atv is not None
            and self._atv.features.get_feature(feature).state is FeatureState.Available
        )

    def capabilities(self) -> InterventionCapabilities:
        observable = self._feature_available(FeatureName.Volume)
        absolute = self._feature_available(FeatureName.SetVolume)
        up = self._feature_available(FeatureName.VolumeUp)
        down = self._feature_available(FeatureName.VolumeDown)
        reasons: list[str] = []
        if not self._connected:
            reasons.append(self._last_error or "not connected")
        if not observable:
            reasons.append("current volume is not observable")
        if not absolute:
            reasons.append("absolute volume control is unavailable")
        return InterventionCapabilities(
            connected=self._connected,
            volume_observable=observable,
            absolute_volume=absolute,
            step_volume_up=up,
            step_volume_down=down,
            safe_automatic_mute=self._connected and observable and absolute,
            reasons=tuple(reasons),
        )

    def _current_volume(self) -> float:
        if self._atv is None:
            raise ConnectionError("Apple TV is not connected")
        volume = float(self._atv.audio.volume)
        if not math.isfinite(volume) or not 0.0 <= volume <= 100.0:
            raise RuntimeError(f"Apple TV reported invalid volume {volume!r}")
        return volume

    async def _restore_owned_volume(self, explanation: str) -> ActionResult:
        if not self._mute_owned or self._pre_mute_volume is None:
            return ActionResult(
                generation=self._generation,
                desired=DesiredAudioState.RESTORED,
                changed=False,
                acknowledged=True,
                explanation="no application-owned mute to restore",
            )
        if self._atv is None or not self._connected:
            return ActionResult(
                generation=self._generation,
                desired=DesiredAudioState.RESTORED,
                changed=False,
                acknowledged=False,
                explanation="cannot restore while Apple TV is disconnected",
            )
        current = self._current_volume()
        if current > 0.5:
            self._mute_owned = False
            self._pre_mute_volume = None
            return ActionResult(
                generation=self._generation,
                desired=DesiredAudioState.RESTORED,
                changed=False,
                acknowledged=True,
                explanation="manual volume change detected; restoration not overwritten",
            )
        restore_to = self._pre_mute_volume
        await self._atv.audio.set_volume(restore_to)
        self._mute_owned = False
        self._pre_mute_volume = None
        return ActionResult(
            generation=self._generation,
            desired=DesiredAudioState.RESTORED,
            changed=True,
            acknowledged=True,
            explanation=explanation,
        )

    async def request(self, desired: DesiredAudioState, generation: int) -> ActionResult:
        async with self._lock:
            if generation < self._generation:
                return ActionResult(
                    generation=generation,
                    desired=desired,
                    changed=False,
                    acknowledged=False,
                    explanation="stale action generation ignored",
                )
            self._generation = generation
            if desired is DesiredAudioState.RESTORED:
                return await self._restore_owned_volume("restored observed pre-mute volume")
            if self._mute_owned:
                return ActionResult(
                    generation=generation,
                    desired=desired,
                    changed=False,
                    acknowledged=True,
                    explanation="application-owned mute already applied",
                )
            capability = self.capabilities()
            if not capability.safe_automatic_mute or self._atv is None:
                return ActionResult(
                    generation=generation,
                    desired=desired,
                    changed=False,
                    acknowledged=False,
                    explanation="safe automatic mute unavailable: " + "; ".join(capability.reasons),
                )
            pre_mute = self._current_volume()
            await self._atv.audio.set_volume(0.0)
            self._pre_mute_volume = pre_mute
            self._mute_owned = True
            return ActionResult(
                generation=generation,
                desired=desired,
                changed=pre_mute > 0.5,
                acknowledged=True,
                explanation="muted after observing restorable volume",
            )

    async def restore_for_shutdown(self) -> ActionResult:
        async with self._lock:
            self._generation += 1
            return await self._restore_owned_volume("restored during shutdown")

    async def disconnect(self) -> None:
        await self.restore_for_shutdown()
        await self._close_transport()
        self._device_id = None

    async def _close_transport(self) -> None:
        if self._atv is not None:
            pending = self._atv.close()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._atv = None
        self._listener = None
        self._connected = False
