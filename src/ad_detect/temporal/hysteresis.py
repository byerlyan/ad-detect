"""Transparent temporal baseline with directional hysteresis."""

from __future__ import annotations

from collections.abc import Sequence

from ad_detect.domain import CaptureHealth, Classification, DetectorEvidence, TemporalState
from ad_detect.domain.models import probabilities


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
        self._state = Classification.PROGRAM
        self._commercial_ticks = 0
        self._program_ticks = 0

    def reset(self) -> None:
        self._state = Classification.PROGRAM
        self._commercial_ticks = 0
        self._program_ticks = 0

    def _health_reasons(self, health: CaptureHealth) -> tuple[str, ...]:
        reasons = list(health.reasons)
        if not health.video_valid:
            reasons.append("video invalid")
        if not health.audio_valid:
            reasons.append("audio invalid")
        if health.black_frame:
            reasons.append("black frame")
        if health.frozen_frame:
            reasons.append("frozen frame")
        if health.stale_ms > self._stale_after_ms:
            reasons.append("capture stale")
        if health.av_skew_ms is not None and abs(health.av_skew_ms) > self._max_av_skew_ms:
            reasons.append("A/V skew exceeds limit")
        return tuple(dict.fromkeys(reasons))

    def update(
        self,
        evidence: Sequence[DetectorEvidence],
        health: CaptureHealth,
    ) -> TemporalState:
        health_reasons = self._health_reasons(health)
        valid = [item for item in evidence if item.valid]
        if health_reasons or not valid:
            # Recovery starts from a safe PROGRAM baseline and must accumulate
            # fresh entry evidence after capture becomes valid again.
            self._state = Classification.PROGRAM
            self._commercial_ticks = 0
            self._program_ticks = 0
            why = health_reasons or ("no valid detector evidence",)
            return TemporalState(
                at_ns=health.at_ns,
                classification=Classification.UNKNOWN,
                confidence=1.0,
                probabilities=probabilities(0.0, 0.0, 1.0),
                commercial_ticks=0,
                program_ticks=0,
                reasons=why,
            )

        commercial = sum(item.probabilities[Classification.COMMERCIAL] for item in valid) / len(
            valid
        )
        program = sum(item.probabilities[Classification.PROGRAM] for item in valid) / len(valid)
        unknown = sum(item.probabilities[Classification.UNKNOWN] for item in valid) / len(valid)
        total = commercial + program + unknown
        fused = probabilities(program / total, commercial / total, unknown / total)
        reasons = tuple(reason for item in valid for reason in item.explanation)

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
        )
