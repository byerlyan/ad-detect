"""Backend-owned setup probes; credentials and subprocesses never reach the browser."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from ad_detect.capture import FFmpegAVFoundation
from ad_detect.domain import CaptureDevice, CaptureProbe, SetupStep
from ad_detect.ports import PairingSession, PlaybackDevice

from .configuration import ConfigurationService
from .workflow import SetupWorkflow


class AppleTVSetupPort(Protocol):
    async def discover(self) -> tuple[PlaybackDevice, ...]: ...

    async def begin_pairing(self, device_id: str, protocol: str) -> PairingSession: ...

    async def finish_pairing(self, token: str, pin: int | None) -> bool: ...


class SetupHardwareService:
    def __init__(
        self,
        configuration: ConfigurationService,
        workflow: SetupWorkflow,
        *,
        capture_factory: Callable[[Any], FFmpegAVFoundation] = FFmpegAVFoundation,
        appletv_factory: Callable[[Any], AppleTVSetupPort] | None = None,
    ) -> None:
        self.configuration = configuration
        self.workflow = workflow
        self._capture_factory = capture_factory
        self._appletv_factory = appletv_factory
        self._appletv: AppleTVSetupPort | None = None

    async def capture_devices(self) -> tuple[CaptureDevice, ...]:
        return await self._capture_factory(self.configuration.settings.capture).discover()

    async def capture_probe(self, *, actor: str = "setup-browser") -> CaptureProbe:
        result = await self._capture_factory(self.configuration.settings.capture).probe()
        if result.success:
            payload = {
                "negotiated_video": result.negotiated_video or "",
                "negotiated_audio": result.negotiated_audio or "",
                "duration_seconds": result.duration_seconds,
            }
            now = time.time_ns()
            if self.workflow.snapshot().current_step is SetupStep.VIDEO:
                self.workflow.verify(
                    SetupStep.VIDEO,
                    config_digest=self.configuration.digest,
                    payload=payload,
                    actor=actor,
                    at_ns=now,
                )
            if self.workflow.snapshot().current_step is SetupStep.AUDIO:
                self.workflow.verify(
                    SetupStep.AUDIO,
                    config_digest=self.configuration.digest,
                    payload=payload,
                    actor=actor,
                    at_ns=now + 1,
                )
        return result

    def _apple_tv(self) -> AppleTVSetupPort:
        if self._appletv is None:
            if self._appletv_factory is None:
                from ad_detect.intervention.appletv import AppleTVIntervention

                self._appletv = AppleTVIntervention(self.configuration.settings.intervention)
            else:
                self._appletv = self._appletv_factory(
                    self.configuration.settings.intervention
                )
        return self._appletv

    async def appletv_devices(self) -> tuple[PlaybackDevice, ...]:
        return await self._apple_tv().discover()

    async def begin_pairing(self, device_id: str, protocol: str) -> PairingSession:
        return await self._apple_tv().begin_pairing(device_id, protocol)

    async def finish_pairing(
        self,
        token: str,
        pin: int | None,
        *,
        actor: str = "setup-browser",
    ) -> bool:
        paired = await self._apple_tv().finish_pairing(token, pin)
        if paired:
            self.workflow.verify(
                SetupStep.APPLE_TV,
                config_digest=self.configuration.digest,
                payload={"paired": True},
                actor=actor,
                at_ns=time.time_ns(),
            )
        return paired

    def readiness(self, *, actor: str = "setup-browser") -> dict[str, Any]:
        settings = self.configuration.settings
        reasons: list[str] = []
        if not settings.capture.video_device:
            reasons.append("video capture device is not selected")
        if not settings.capture.audio_device:
            reasons.append("audio capture device is not selected")
        if settings.ml.enabled and settings.ml.use_registry:
            if self.workflow.store.active_model() is None:
                reasons.append("ML registry has no active compatible model")
        ready = not reasons
        payload: dict[str, Any] = {"ready": ready, "reasons": reasons}
        if ready:
            self.workflow.verify(
                SetupStep.READINESS,
                config_digest=self.configuration.digest,
                payload=payload,
                actor=actor,
                at_ns=time.time_ns(),
            )
        return payload
