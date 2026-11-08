"""Small transparent three-state hidden Markov temporal classifier."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ad_detect.domain import CaptureHealth, Classification, DetectorEvidence, TemporalState
from ad_detect.domain.models import probabilities

from .observation import fuse_observation


class HiddenMarkovFusion:
    def __init__(
        self,
        *,
        stay_probability: float,
        stale_after_ms: float,
        max_av_skew_ms: float,
        evidence_deadline_ms: float,
    ) -> None:
        if not 0.5 <= stay_probability < 1:
            raise ValueError("HMM stay probability must be in [0.5, 1)")
        change = (1.0 - stay_probability) / 2.0
        self._transition = np.full((3, 3), change, dtype=np.float64)
        np.fill_diagonal(self._transition, stay_probability)
        self._stale_after_ms = stale_after_ms
        self._max_av_skew_ms = max_av_skew_ms
        self._evidence_deadline_ms = evidence_deadline_ms
        self._classes = tuple(Classification)
        self._posterior = np.asarray([0.98, 0.01, 0.01], dtype=np.float64)

    def reset(self) -> None:
        self._posterior = np.asarray([0.98, 0.01, 0.01], dtype=np.float64)

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
        prior = self._posterior @ self._transition
        emission = np.asarray(
            [max(observation.probabilities[item], 1e-9) for item in self._classes],
            dtype=np.float64,
        )
        posterior = prior * emission
        self._posterior = posterior / posterior.sum()
        selected_index = int(np.argmax(self._posterior))
        selected = self._classes[selected_index]
        fused = probabilities(*[float(value) for value in self._posterior])
        reasons = (
            *observation.reasons,
            "HMM posterior "
            + " ".join(
                f"{classification.value}={self._posterior[index]:.3f}"
                for index, classification in enumerate(self._classes)
            ),
        )
        return TemporalState(
            at_ns=health.at_ns,
            classification=selected,
            confidence=float(self._posterior[selected_index]),
            probabilities=fused,
            commercial_ticks=int(selected is Classification.COMMERCIAL),
            program_ticks=int(selected is Classification.PROGRAM),
            reasons=tuple(dict.fromkeys(reasons)),
            evidence_versions=observation.evidence_versions,
        )
