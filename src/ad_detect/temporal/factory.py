"""Construction boundary for configured temporal strategies."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ad_detect.config.settings import CaptureSettings, DetectionSettings, TemporalSettings
from ad_detect.domain import CaptureHealth, DetectorEvidence, TemporalState, TemporalStrategy

from .hmm import HiddenMarkovFusion
from .hysteresis import HysteresisFusion
from .sliding import SlidingWindowFusion


class TemporalClassifier(Protocol):
    def reset(self) -> None: ...

    def update(
        self,
        evidence: Sequence[DetectorEvidence],
        health: CaptureHealth,
    ) -> TemporalState: ...


def build_temporal_classifier(
    strategy: TemporalStrategy,
    detection: DetectionSettings,
    temporal: TemporalSettings,
    capture: CaptureSettings,
) -> TemporalClassifier:
    common = {
        "stale_after_ms": capture.stale_after_ms,
        "max_av_skew_ms": capture.max_av_skew_ms,
        "evidence_deadline_ms": temporal.evidence_deadline_ms,
    }
    if strategy is TemporalStrategy.HYSTERESIS:
        return HysteresisFusion(
            enter_threshold=detection.enter_threshold,
            exit_threshold=detection.exit_threshold,
            enter_ticks=detection.enter_ticks,
            exit_ticks=detection.exit_ticks,
            **common,
        )
    if strategy is TemporalStrategy.SLIDING_WINDOW:
        return SlidingWindowFusion(
            enter_threshold=detection.enter_threshold,
            exit_threshold=detection.exit_threshold,
            window_ticks=temporal.sliding_window_ticks,
            decay=temporal.sliding_decay,
            **common,
        )
    return HiddenMarkovFusion(
        stay_probability=temporal.hmm_stay_probability,
        **common,
    )
