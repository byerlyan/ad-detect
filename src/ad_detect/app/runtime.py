"""Composition-friendly runtime for one causal detection tick at a time."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from ad_detect.capture.simulated import SimulatedTick
from ad_detect.config import Settings
from ad_detect.data import SQLiteStore
from ad_detect.domain import (
    CaptureHealth,
    Classification,
    Decision,
    DetectorEvidence,
    RecommendedAction,
    TemporalState,
)
from ad_detect.domain.models import probabilities
from ad_detect.intervention import SimulatedIntervention
from ad_detect.policy import ConservativeDecisionPolicy
from ad_detect.ports import DesiredAudioState, InterventionPort
from ad_detect.temporal import build_temporal_classifier


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    session_id: str
    running: bool
    ticks_processed: int
    temporal: TemporalState | None
    decision: Decision | None
    audio_state: str
    manual_program_override: bool
    mute_lease_active: bool


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
        self.fusion = build_temporal_classifier(
            settings.temporal.strategy,
            settings.detection,
            settings.temporal,
            settings.capture,
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
        self._manual_program_override = False
        self._mute_lease_deadline: float | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._action_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._watchdog_task = asyncio.create_task(
            self._watchdog(),
            name=f"mute-watchdog-{self.session_id}",
        )

    async def stop(self) -> None:
        self._running = False
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog_task
            self._watchdog_task = None
        self._mute_lease_deadline = None
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
            manual_program_override=self._manual_program_override,
        )
        self._ticks += 1
        self._temporal = temporal
        self._decision = decision
        self._persist_decision(decision)
        if (
            decision.classification is Classification.COMMERCIAL
            and decision.break_active
            and self.intervention.audio_state is DesiredAudioState.MUTED
            and not self._manual_program_override
        ):
            self._renew_mute_lease()
        await self._dispatch(decision)
        return decision

    async def set_manual_program_override(
        self,
        enabled: bool,
        *,
        at_ns: int | None = None,
    ) -> Decision | None:
        """Set the operator safety override and restore immediately when possible."""
        self._manual_program_override = enabled
        event_at_ns = at_ns if at_ns is not None else time.monotonic_ns()
        self.store.append_event(
            session_id=self.session_id,
            at_ns=event_at_ns,
            kind="manual.override",
            payload={"program_override": enabled},
        )
        if not enabled or self._temporal is None:
            return None
        current = replace(self._temporal, at_ns=event_at_ns)
        decision = self.policy.decide(
            current,
            mode=self.settings.intervention.mode,
            automatic_muting_enabled=self.settings.intervention.automatic_muting,
            manual_program_override=True,
        )
        self._decision = decision
        self._persist_decision(decision)
        await self._dispatch(decision)
        return decision

    def _persist_decision(self, decision: Decision) -> None:
        self.store.append_event(
            session_id=self.session_id,
            at_ns=decision.at_ns,
            kind="decision",
            payload=decision_payload(decision),
        )

    async def _dispatch(self, decision: Decision) -> None:
        if decision.action is RecommendedAction.NONE:
            return
        async with self._action_lock:
            self._generation += 1
            desired = (
                DesiredAudioState.MUTED
                if decision.action is RecommendedAction.MUTE
                else DesiredAudioState.RESTORED
            )
            result = await self.intervention.request(desired, self._generation)
            if desired is DesiredAudioState.MUTED and result.acknowledged:
                self._renew_mute_lease()
            elif desired is DesiredAudioState.RESTORED:
                self._mute_lease_deadline = None
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

    def _renew_mute_lease(self) -> None:
        self._mute_lease_deadline = (
            asyncio.get_running_loop().time() + self.settings.detection.mute_lease_ms / 1000
        )

    async def _watchdog(self) -> None:
        interval = self.settings.detection.watchdog_interval_ms / 1000
        while self._running:
            await asyncio.sleep(interval)
            deadline = self._mute_lease_deadline
            if deadline is None or asyncio.get_running_loop().time() < deadline:
                continue
            self._mute_lease_deadline = None
            at_ns = time.monotonic_ns()
            temporal = TemporalState(
                at_ns=at_ns,
                classification=Classification.UNKNOWN,
                confidence=1.0,
                probabilities=probabilities(0.0, 0.0, 1.0),
                commercial_ticks=0,
                program_ticks=0,
                reasons=("mute lease expired; watchdog requested restoration",),
            )
            decision = self.policy.decide(
                temporal,
                mode=self.settings.intervention.mode,
                automatic_muting_enabled=self.settings.intervention.automatic_muting,
            )
            self._temporal = temporal
            self._decision = decision
            self._persist_decision(decision)
            self.store.append_event(
                session_id=self.session_id,
                at_ns=at_ns,
                kind="intervention.watchdog",
                payload={"reason": "mute lease expired"},
            )
            await self._dispatch(decision)

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            session_id=self.session_id,
            running=self._running,
            ticks_processed=self._ticks,
            temporal=self._temporal,
            decision=self._decision,
            audio_state=self.intervention.audio_state.value,
            manual_program_override=self._manual_program_override,
            mute_lease_active=self._mute_lease_deadline is not None,
        )
