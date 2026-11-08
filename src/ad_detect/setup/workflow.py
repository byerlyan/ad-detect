"""Resumable, dependency-aware first-time setup state machine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ad_detect.data import SQLiteStore
from ad_detect.domain import (
    SETUP_STEP_ORDER,
    SetupSnapshot,
    SetupStep,
    SetupStepResult,
    SetupStepStatus,
)

_CLIENT_ACKNOWLEDGEMENTS = {SetupStep.WELCOME, SetupStep.MODE}
_SKIPPABLE = {SetupStep.APPLE_TV, SetupStep.CALIBRATION}


class SetupWorkflow:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def snapshot(self) -> SetupSnapshot:
        completed = tuple(self.store.setup_step_results())
        completed_steps = {result.step for result in completed}
        current = next((step for step in SETUP_STEP_ORDER if step not in completed_steps), None)
        readiness = next(
            (result for result in completed if result.step is SetupStep.READINESS),
            None,
        )
        shadow = next((result for result in completed if result.step is SetupStep.SHADOW), None)
        ready_for_shadow = bool(readiness and readiness.payload.get("ready") is True)
        automatic_available = bool(
            shadow
            and shadow.payload.get("passed") is True
            and shadow.payload.get("safe_intervention") is True
            and shadow.payload.get("restore_verified") is True
        )
        return SetupSnapshot(
            current_step=current,
            completed=completed,
            ready_for_shadow=ready_for_shadow,
            automatic_consent_available=automatic_available,
        )

    def acknowledge(
        self,
        step: SetupStep,
        *,
        config_digest: str,
        payload: Mapping[str, Any],
        actor: str,
        at_ns: int,
    ) -> SetupStepResult:
        if step not in _CLIENT_ACKNOWLEDGEMENTS:
            raise PermissionError(f"setup step {step.value} requires server verification")
        return self._record(
            step,
            SetupStepStatus.COMPLETE,
            config_digest=config_digest,
            payload=payload,
            actor=actor,
            at_ns=at_ns,
        )

    def verify(
        self,
        step: SetupStep,
        *,
        config_digest: str,
        payload: Mapping[str, Any],
        actor: str,
        at_ns: int,
    ) -> SetupStepResult:
        if step is SetupStep.READINESS and payload.get("ready") is not True:
            raise ValueError("readiness cannot complete with a failing result")
        if step is SetupStep.SHADOW and not {
            "passed",
            "safe_intervention",
            "restore_verified",
        } <= {key for key, value in payload.items() if value is True}:
            raise ValueError("shadow validation requires safety and restoration evidence")
        if step is SetupStep.AUTO_MUTE_CONSENT:
            if not self.snapshot().automatic_consent_available:
                raise ValueError("automatic consent prerequisites are incomplete")
            if payload.get("explicit_consent") is not True:
                raise ValueError("automatic mute requires explicit consent")
        return self._record(
            step,
            SetupStepStatus.COMPLETE,
            config_digest=config_digest,
            payload=payload,
            actor=actor,
            at_ns=at_ns,
        )

    def skip(
        self,
        step: SetupStep,
        *,
        config_digest: str,
        reason: str,
        actor: str,
        at_ns: int,
    ) -> SetupStepResult:
        if step not in _SKIPPABLE:
            raise ValueError(f"setup step {step.value} cannot be skipped")
        if not reason.strip():
            raise ValueError("skipped setup steps require a reason")
        return self._record(
            step,
            SetupStepStatus.SKIPPED,
            config_digest=config_digest,
            payload={"reason": reason},
            actor=actor,
            at_ns=at_ns,
        )

    def invalidate_from(
        self,
        step: SetupStep,
        *,
        config_digest: str,
        reason: str,
        actor: str,
        at_ns: int,
    ) -> None:
        start = SETUP_STEP_ORDER.index(step)
        self.store.delete_setup_step_results(
            set(SETUP_STEP_ORDER[start:]),
            config_digest=config_digest,
            actor=actor,
            reason=reason,
            at_ns=at_ns,
        )

    def _record(
        self,
        step: SetupStep,
        status: SetupStepStatus,
        *,
        config_digest: str,
        payload: Mapping[str, Any],
        actor: str,
        at_ns: int,
    ) -> SetupStepResult:
        current = self.snapshot().current_step
        if current is not step:
            expected = "complete" if current is None else current.value
            raise ValueError(f"setup step {step.value} is out of order; expected {expected}")
        result = SetupStepResult(
            step=step,
            status=status,
            config_digest=config_digest,
            payload=dict(payload),
            actor=actor,
            completed_at_ns=at_ns,
        )
        self.store.save_setup_step_result(result)
        self.store.append_setup_transition(
            result,
            action="skip" if status is SetupStepStatus.SKIPPED else "complete",
        )
        return result
