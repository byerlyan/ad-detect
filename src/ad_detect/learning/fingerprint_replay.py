"""Causal fingerprint-only replay through the continuity-guarded detector."""

from __future__ import annotations

import time
from collections.abc import Sequence

from ad_detect.config.settings import FingerprintSettings
from ad_detect.detectors import FingerprintDetector, FingerprintQueryIndex
from ad_detect.domain import LabelRevision, SynchronizedWindow

from .replay import ReplayPrediction, ReplayResult, replay_metrics


class FingerprintReplayEvaluator:
    def __init__(self, index: FingerprintQueryIndex, settings: FingerprintSettings) -> None:
        self.index = index
        self.settings = settings

    async def evaluate(
        self,
        windows: Sequence[SynchronizedWindow],
        labels: Sequence[LabelRevision],
    ) -> ReplayResult:
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        predictions: list[ReplayPrediction] = []
        detector: FingerprintDetector | None = None
        current_session: str | None = None
        for window in sorted(windows, key=lambda item: (item.session_id, item.start_ns)):
            if window.session_id != current_session:
                current_session = window.session_id
                detector = FingerprintDetector(self.index, self.settings)
            assert detector is not None
            started = time.perf_counter()
            evidence = await detector.infer(
                window.audio,
                window_start_ns=window.start_ns,
                window_end_ns=window.end_ns,
                health=window.health,
            )
            classification = max(evidence.probabilities, key=evidence.probabilities.__getitem__)
            predictions.append(
                ReplayPrediction(
                    session_id=window.session_id,
                    start_ns=window.start_ns,
                    end_ns=window.end_ns,
                    classification=classification,
                    confidence=evidence.probabilities[classification],
                    inference_ms=(time.perf_counter() - started) * 1000,
                )
            )
        metrics = replay_metrics(
            predictions,
            labels,
            wall_seconds=time.perf_counter() - wall_started,
            cpu_seconds=time.process_time() - cpu_started,
        )
        return ReplayResult(tuple(predictions), metrics)
