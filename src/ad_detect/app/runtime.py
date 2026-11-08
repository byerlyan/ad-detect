"""Composition-friendly runtime for one causal detection tick at a time."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ad_detect.capture.simulated import SimulatedTick
from ad_detect.config import Settings
from ad_detect.data import SQLiteStore
from ad_detect.domain import (
    CaptureHealth,
    Decision,
    DetectorEvidence,
    RecommendedAction,
    TemporalState,
)
from ad_detect.intervention import SimulatedIntervention
from ad_detect.policy import ConservativeDecisionPolicy
from ad_detect.ports import DesiredAudioState, InterventionPort
from ad_detect.temporal import HysteresisFusion


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    session_id: str
    running: bool
    ticks_processed: int
    temporal: TemporalState | None
    decision: Decision | None
    audio_state: str


def decision_payload(decision: Decision) -> dict[str, Any]:
    return {
        "classification": decision.classification.value,
        "confidence": round(decision.confidence, 9),
        "break_active": decision.break_active,
        "action": decision.action.value,
        "reasons": list(decision.reasons),
        "expires_ns": decision.expires_ns,
        "evidence_versions": dict(sorted(decision.evidence_versions.items())),
    }


class DetectionRuntime:
    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        *,
        session_id: str = "local-session",
        intervention: InterventionPort | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.session_id = session_id
        self.intervention = intervention or SimulatedIntervention()
        self.fusion = HysteresisFusion(
            enter_threshold=settings.detection.enter_threshold,
            exit_threshold=settings.detection.exit_threshold,
            enter_ticks=settings.detection.enter_ticks,
            exit_ticks=settings.detection.exit_ticks,
            stale_after_ms=settings.capture.stale_after_ms,
            max_av_skew_ms=settings.capture.max_av_skew_ms,
        )
        self.policy = ConservativeDecisionPolicy(
            maximum_mute_seconds=settings.detection.maximum_mute_seconds,
            reentry_cooldown_seconds=settings.detection.reentry_cooldown_seconds,
            decision_ttl_ms=max(100, settings.detection.tick_interval_ms * 2),
        )
        self._running = False
        self._ticks = 0
        self._generation = 0
        self._temporal: TemporalState | None = None
        self._decision: Decision | None = None

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        result = await self.intervention.restore_for_shutdown()
        self.store.append_event(
            session_id=self.session_id,
            at_ns=self._temporal.at_ns if self._temporal else 0,
            kind="intervention.shutdown_restore",
            payload={
                "generation": result.generation,
                "changed": result.changed,
                "acknowledged": result.acknowledged,
            },
        )
        await self.intervention.disconnect()

    async def process_tick(self, tick: SimulatedTick) -> Decision:
        return await self.process_evidence(tick.evidence, tick.health)

    async def process_evidence(
        self,
        evidence: Sequence[DetectorEvidence],
        health: CaptureHealth,
    ) -> Decision:
        if not self._running:
            raise RuntimeError("runtime is not running")
        temporal = self.fusion.update(evidence, health)
        decision = self.policy.decide(
            temporal,
            mode=self.settings.intervention.mode,
            automatic_muting_enabled=self.settings.intervention.automatic_muting,
        )
        self._ticks += 1
        self._temporal = temporal
        self._decision = decision
        self.store.append_event(
            session_id=self.session_id,
            at_ns=decision.at_ns,
            kind="decision",
            payload=decision_payload(decision),
        )
        if decision.action is not RecommendedAction.NONE:
            self._generation += 1
            desired = (
                DesiredAudioState.MUTED
                if decision.action is RecommendedAction.MUTE
                else DesiredAudioState.RESTORED
            )
            result = await self.intervention.request(desired, self._generation)
            self.store.append_event(
                session_id=self.session_id,
                at_ns=decision.at_ns,
                kind="intervention.action",
                payload={
                    "generation": result.generation,
                    "desired": result.desired.value,
                    "changed": result.changed,
                    "acknowledged": result.acknowledged,
                    "explanation": result.explanation,
                },
            )
        return decision

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            session_id=self.session_id,
            running=self._running,
            ticks_processed=self._ticks,
            temporal=self._temporal,
            decision=self._decision,
            audio_state=self.intervention.audio_state.value,
        )
