"""Causal replay for one immutable tabular ML candidate."""

from __future__ import annotations

import time
from collections.abc import Sequence

from ad_detect.detectors import SklearnModelDetector
from ad_detect.domain import Classification, FeatureWindow, LabelRevision

from .replay import ReplayPrediction, ReplayResult, replay_metrics


class MLReplayEvaluator:
    def __init__(self, detector: SklearnModelDetector) -> None:
        self.detector = detector

    async def evaluate(
        self,
        windows: Sequence[FeatureWindow],
        labels: Sequence[LabelRevision],
    ) -> ReplayResult:
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        predictions: list[ReplayPrediction] = []
        for window in sorted(windows, key=lambda item: (item.session_id, item.start_ns)):
            started = time.perf_counter()
            evidence = await self.detector.infer(window)
            classification = max(evidence.probabilities, key=evidence.probabilities.__getitem__)
            if not evidence.valid:
                classification = Classification.UNKNOWN
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
