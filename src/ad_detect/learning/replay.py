"""Causal rules-baseline replay and safety-oriented evaluation metrics."""

from __future__ import annotations

import resource
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from statistics import fmean

from ad_detect.config.settings import DetectionSettings, RuleSettings
from ad_detect.detectors import RulesDetector
from ad_detect.domain import CaptureHealth, Classification, FeatureWindow, LabelRevision
from ad_detect.temporal import HysteresisFusion

_NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class ReplayPrediction:
    session_id: str
    start_ns: int
    end_ns: int
    classification: Classification
    confidence: float
    inference_ms: float


@dataclass(frozen=True, slots=True)
class ReplayMetrics:
    confusion_seconds: Mapping[str, Mapping[str, float]]
    labeled_seconds: float
    unlabeled_seconds: float
    precision: float
    recall: float
    f1: float
    commercial_mute_coverage: float
    false_muted_program_seconds_per_hour: float
    predicted_unknown_seconds: float
    mute_start_latency_seconds: float | None
    unmute_latency_seconds: float | None
    boundary_error_seconds: float | None
    missed_commercial_spans: int
    inference_mean_ms: float
    inference_p95_ms: float
    inference_max_ms: float
    replay_wall_seconds: float
    cpu_seconds: float
    peak_rss_bytes: int


@dataclass(frozen=True, slots=True)
class ReplayResult:
    predictions: tuple[ReplayPrediction, ...]
    metrics: ReplayMetrics


@dataclass(frozen=True, slots=True)
class _ActionSpan:
    session_id: str
    start_ns: int
    end_ns: int
    classification: Classification


class RulesReplayEvaluator:
    """Replay feature windows in timestamp order without future information."""

    def __init__(
        self,
        rules: RuleSettings,
        detection: DetectionSettings,
        *,
        feature_schema: str,
        stale_after_ms: float = 1500.0,
        max_av_skew_ms: float = 150.0,
    ) -> None:
        self._detector = RulesDetector(rules, feature_schema=feature_schema)
        self._detection = detection
        self._stale_after_ms = stale_after_ms
        self._max_av_skew_ms = max_av_skew_ms

    def _fusion(self) -> HysteresisFusion:
        return HysteresisFusion(
            enter_threshold=self._detection.enter_threshold,
            exit_threshold=self._detection.exit_threshold,
            enter_ticks=self._detection.enter_ticks,
            exit_ticks=self._detection.exit_ticks,
            stale_after_ms=self._stale_after_ms,
            max_av_skew_ms=self._max_av_skew_ms,
        )

    async def evaluate(
        self,
        windows: Sequence[FeatureWindow],
        labels: Sequence[LabelRevision],
    ) -> ReplayResult:
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        predictions: list[ReplayPrediction] = []
        fusion: HysteresisFusion | None = None
        current_session: str | None = None

        for window in sorted(windows, key=lambda item: (item.session_id, item.start_ns)):
            if window.session_id != current_session:
                current_session = window.session_id
                fusion = self._fusion()
            assert fusion is not None
            started = time.perf_counter()
            evidence = await self._detector.infer(window)
            missing_capture = "capture" in window.missing
            missing_audio = missing_capture or "audio" in window.missing
            missing_video = missing_capture or "video" in window.missing
            reasons = tuple(f"missing {name}" for name in sorted(window.missing))
            health = CaptureHealth(
                at_ns=window.end_ns,
                video_valid=not missing_video,
                audio_valid=not missing_audio,
                stale_ms=0.0,
                reasons=reasons,
            )
            state = fusion.update((evidence,), health)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            predictions.append(
                ReplayPrediction(
                    session_id=window.session_id,
                    start_ns=window.start_ns,
                    end_ns=window.end_ns,
                    classification=state.classification,
                    confidence=state.confidence,
                    inference_ms=elapsed_ms,
                )
            )

        metrics = _metrics(
            predictions,
            labels,
            wall_seconds=time.perf_counter() - wall_started,
            cpu_seconds=time.process_time() - cpu_started,
        )
        return ReplayResult(tuple(predictions), metrics)


def _metrics(
    predictions: Sequence[ReplayPrediction],
    labels: Sequence[LabelRevision],
    *,
    wall_seconds: float,
    cpu_seconds: float,
) -> ReplayMetrics:
    classes = tuple(Classification)
    confusion_ns = {truth: {prediction: 0 for prediction in classes} for truth in classes}
    unlabeled_ns = 0
    predicted_unknown_ns = 0
    labels_by_session: dict[str, list[LabelRevision]] = {}
    for label in labels:
        labels_by_session.setdefault(label.session_id, []).append(label)

    action_spans = _action_spans(predictions)
    for prediction in action_spans:
        if prediction.classification is Classification.UNKNOWN:
            predicted_unknown_ns += prediction.end_ns - prediction.start_ns
        relevant = [
            label
            for label in labels_by_session.get(prediction.session_id, ())
            if label.start_ns < prediction.end_ns and label.end_ns > prediction.start_ns
        ]
        boundaries = {prediction.start_ns, prediction.end_ns}
        for label in relevant:
            boundaries.add(max(prediction.start_ns, label.start_ns))
            boundaries.add(min(prediction.end_ns, label.end_ns))
        ordered = sorted(boundaries)
        for start_ns, end_ns in pairwise(ordered):
            active = {
                label.label
                for label in relevant
                if label.start_ns < end_ns and label.end_ns > start_ns
            }
            duration = end_ns - start_ns
            if not active:
                unlabeled_ns += duration
                continue
            truth = next(iter(active)) if len(active) == 1 else Classification.UNKNOWN
            confusion_ns[truth][prediction.classification] += duration

    true_positive = confusion_ns[Classification.COMMERCIAL][Classification.COMMERCIAL]
    false_positive = sum(
        confusion_ns[truth][Classification.COMMERCIAL]
        for truth in classes
        if truth is not Classification.COMMERCIAL
    )
    false_negative = sum(
        confusion_ns[Classification.COMMERCIAL][prediction]
        for prediction in classes
        if prediction is not Classification.COMMERCIAL
    )
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = _ratio(2 * precision * recall, precision + recall)
    program_ns = sum(confusion_ns[Classification.PROGRAM].values())
    false_muted_ns = confusion_ns[Classification.PROGRAM][Classification.COMMERCIAL]

    commercial_spans = _merged_commercial_label_spans(labels)
    predicted_spans = _merged_commercial_prediction_spans(predictions)
    start_latencies: list[float] = []
    end_latencies: list[float] = []
    boundary_errors: list[float] = []
    missed = 0
    for session_id, truth_start, truth_end in commercial_spans:
        overlaps = [
            (start_ns, end_ns)
            for candidate_session, start_ns, end_ns in predicted_spans
            if candidate_session == session_id and start_ns < truth_end and end_ns > truth_start
        ]
        if not overlaps:
            missed += 1
            continue
        predicted_start, predicted_end = min(
            overlaps,
            key=lambda span: abs(span[0] - truth_start) + abs(span[1] - truth_end),
        )
        start_latencies.append(max(0, predicted_start - truth_start) / _NS_PER_SECOND)
        end_latencies.append(max(0, predicted_end - truth_end) / _NS_PER_SECOND)
        boundary_errors.extend(
            (
                abs(predicted_start - truth_start) / _NS_PER_SECOND,
                abs(predicted_end - truth_end) / _NS_PER_SECOND,
            )
        )

    inference = [prediction.inference_ms for prediction in predictions]
    confusion_seconds = {
        truth.value: {
            prediction.value: confusion_ns[truth][prediction] / _NS_PER_SECOND
            for prediction in classes
        }
        for truth in classes
    }
    labeled_ns = sum(sum(row.values()) for row in confusion_ns.values())
    return ReplayMetrics(
        confusion_seconds=confusion_seconds,
        labeled_seconds=labeled_ns / _NS_PER_SECOND,
        unlabeled_seconds=unlabeled_ns / _NS_PER_SECOND,
        precision=precision,
        recall=recall,
        f1=f1,
        commercial_mute_coverage=recall,
        false_muted_program_seconds_per_hour=_ratio(false_muted_ns, program_ns) * 3600.0,
        predicted_unknown_seconds=predicted_unknown_ns / _NS_PER_SECOND,
        mute_start_latency_seconds=fmean(start_latencies) if start_latencies else None,
        unmute_latency_seconds=fmean(end_latencies) if end_latencies else None,
        boundary_error_seconds=fmean(boundary_errors) if boundary_errors else None,
        missed_commercial_spans=missed,
        inference_mean_ms=fmean(inference) if inference else 0.0,
        inference_p95_ms=_percentile(inference, 0.95),
        inference_max_ms=max(inference, default=0.0),
        replay_wall_seconds=wall_seconds,
        cpu_seconds=cpu_seconds,
        peak_rss_bytes=_peak_rss_bytes(),
    )


def _merged_commercial_label_spans(
    labels: Sequence[LabelRevision],
) -> list[tuple[str, int, int]]:
    spans = sorted(
        (label.session_id, label.start_ns, label.end_ns)
        for label in labels
        if label.label is Classification.COMMERCIAL
    )
    return _merge_spans(spans)


def _merged_commercial_prediction_spans(
    predictions: Sequence[ReplayPrediction],
) -> list[tuple[str, int, int]]:
    spans = sorted(
        (prediction.session_id, prediction.start_ns, prediction.end_ns)
        for prediction in _action_spans(predictions)
        if prediction.classification is Classification.COMMERCIAL
    )
    return _merge_spans(spans)


def _action_spans(predictions: Sequence[ReplayPrediction]) -> list[_ActionSpan]:
    """Apply a causal decision from its window end until the next session tick."""
    spans: list[_ActionSpan] = []
    ordered = sorted(predictions, key=lambda item: (item.session_id, item.end_ns))
    for current, following in pairwise(ordered):
        if current.session_id != following.session_id or following.end_ns <= current.end_ns:
            continue
        spans.append(
            _ActionSpan(
                session_id=current.session_id,
                start_ns=current.end_ns,
                end_ns=following.end_ns,
                classification=current.classification,
            )
        )
    return spans


def _merge_spans(spans: Sequence[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    merged: list[tuple[str, int, int]] = []
    for session_id, start_ns, end_ns in spans:
        if merged and merged[-1][0] == session_id and start_ns <= merged[-1][2]:
            previous_session, previous_start, previous_end = merged[-1]
            merged[-1] = (previous_session, previous_start, max(previous_end, end_ns))
        else:
            merged.append((session_id, start_ns, end_ns))
    return merged


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * quantile)))
    return ordered[index]


def _peak_rss_bytes() -> int:
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return rss if sys.platform == "darwin" else rss * 1024
