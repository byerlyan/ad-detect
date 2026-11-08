"""Recency-weighted sliding-window temporal classifier."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from ad_detect.domain import CaptureHealth, Classification, DetectorEvidence, TemporalState
from ad_detect.domain.models import probabilities

from .observation import FusedObservation, fuse_observation


class SlidingWindowFusion:
    def __init__(
        self,
        *,
        enter_threshold: float,
        exit_threshold: float,
        window_ticks: int,
        decay: float,
        stale_after_ms: float,
        max_av_skew_ms: float,
        evidence_deadline_ms: float,
    ) -> None:
        if not 0 <= exit_threshold <= enter_threshold <= 1:
            raise ValueError("thresholds must satisfy 0 <= exit <= enter <= 1")
        if window_ticks < 2 or not 0 < decay <= 1:
            raise ValueError("sliding window configuration is invalid")
        self._enter_threshold = enter_threshold
        self._exit_threshold = exit_threshold
        self._history: deque[FusedObservation] = deque(maxlen=window_ticks)
        self._decay = decay
        self._stale_after_ms = stale_after_ms
        self._max_av_skew_ms = max_av_skew_ms
        self._evidence_deadline_ms = evidence_deadline_ms
        self._state = Classification.PROGRAM

    def reset(self) -> None:
        self._history.clear()
        self._state = Classification.PROGRAM

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
            self.reset()
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
        self._history.append(observation)
        weighted = {classification: 0.0 for classification in Classification}
        weights = [self._decay**age for age in reversed(range(len(self._history)))]
        weight_total = sum(weights)
        for weight, item in zip(weights, self._history, strict=True):
            for classification in Classification:
                weighted[classification] += weight * item.probabilities[classification]
        fused = probabilities(
            weighted[Classification.PROGRAM] / weight_total,
            weighted[Classification.COMMERCIAL] / weight_total,
            weighted[Classification.UNKNOWN] / weight_total,
        )
        reasons = list(observation.reasons)
        if (
            self._state is not Classification.COMMERCIAL
            and fused[Classification.COMMERCIAL] >= self._enter_threshold
        ):
            self._state = Classification.COMMERCIAL
            reasons.append("sliding-window commercial threshold satisfied")
        elif (
            self._state is Classification.COMMERCIAL
            and fused[Classification.PROGRAM] >= self._exit_threshold
        ):
            self._state = Classification.PROGRAM
            reasons.append("sliding-window program threshold satisfied")
        reasons.append(f"sliding-window observations={len(self._history)} decay={self._decay:.3f}")
        return TemporalState(
            at_ns=health.at_ns,
            classification=self._state,
            confidence=fused[self._state],
            probabilities=fused,
            commercial_ticks=sum(
                item.probabilities[Classification.COMMERCIAL] >= self._enter_threshold
                for item in self._history
            ),
            program_ticks=sum(
                item.probabilities[Classification.PROGRAM] >= self._exit_threshold
                for item in self._history
            ),
            reasons=tuple(dict.fromkeys(reasons)),
            evidence_versions=observation.evidence_versions,
        )
