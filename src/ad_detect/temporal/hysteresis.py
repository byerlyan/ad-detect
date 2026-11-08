"""Transparent temporal baseline with directional hysteresis."""

from __future__ import annotations

from collections.abc import Sequence

from ad_detect.domain import CaptureHealth, Classification, DetectorEvidence, TemporalState

from .observation import fuse_observation


class HysteresisFusion:
    def __init__(
        self,
        *,
        enter_threshold: float,
        exit_threshold: float,
        enter_ticks: int,
        exit_ticks: int,
        stale_after_ms: float,
        max_av_skew_ms: float,
        evidence_deadline_ms: float = 1500.0,
    ) -> None:
        if not 0 <= exit_threshold <= enter_threshold <= 1:
            raise ValueError("thresholds must satisfy 0 <= exit <= enter <= 1")
        if enter_ticks < 1 or exit_ticks < 1:
            raise ValueError("tick counts must be positive")
        self._enter_threshold = enter_threshold
        self._exit_threshold = exit_threshold
        self._enter_ticks = enter_ticks
        self._exit_ticks = exit_ticks
        self._stale_after_ms = stale_after_ms
        self._max_av_skew_ms = max_av_skew_ms
        self._evidence_deadline_ms = evidence_deadline_ms
        self._state = Classification.PROGRAM
        self._commercial_ticks = 0
        self._program_ticks = 0

    def reset(self) -> None:
        self._state = Classification.PROGRAM
        self._commercial_ticks = 0
        self._program_ticks = 0

    def update(
        self,
        evidence: Sequence[DetectorEvidence],
        health: CaptureHealth,
    ) -> TemporalState:
        observation = fuse_observation(
            evidence,
            health,
            stale_after_ms=self._stale_after_ms,
            max_av_skew_ms=self._max_av_skew_ms,
            evidence_deadline_ms=self._evidence_deadline_ms,
        )
        if not observation.valid:
            # Recovery starts from a safe PROGRAM baseline and must accumulate
            # fresh entry evidence after capture becomes valid again.
            self._state = Classification.PROGRAM
            self._commercial_ticks = 0
            self._program_ticks = 0
            return TemporalState(
                at_ns=health.at_ns,
                classification=Classification.UNKNOWN,
                confidence=1.0,
                probabilities=observation.probabilities,
                commercial_ticks=0,
                program_ticks=0,
                reasons=observation.reasons,
                evidence_versions=observation.evidence_versions,
            )

        fused = observation.probabilities
        commercial = fused[Classification.COMMERCIAL]
        program = fused[Classification.PROGRAM]
        reasons = observation.reasons

        if commercial >= self._enter_threshold:
            self._commercial_ticks += 1
        else:
            self._commercial_ticks = 0

        if program >= self._exit_threshold:
            self._program_ticks += 1
        else:
            self._program_ticks = 0

        if (
            self._state is not Classification.COMMERCIAL
            and self._commercial_ticks >= self._enter_ticks
        ):
            self._state = Classification.COMMERCIAL
            self._program_ticks = 0
            reasons += ("commercial entry hysteresis satisfied",)
        elif self._state is Classification.COMMERCIAL and self._program_ticks >= self._exit_ticks:
            self._state = Classification.PROGRAM
            self._commercial_ticks = 0
            reasons += ("program recovery hysteresis satisfied",)

        confidence = fused[self._state]
        return TemporalState(
            at_ns=health.at_ns,
            classification=self._state,
            confidence=confidence,
            probabilities=fused,
            commercial_ticks=self._commercial_ticks,
            program_ticks=self._program_ticks,
            reasons=reasons,
            evidence_versions=observation.evidence_versions,
        )
