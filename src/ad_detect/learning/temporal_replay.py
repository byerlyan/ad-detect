"""Identical causal replay comparison for every temporal strategy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ad_detect.config.settings import DetectionSettings, RuleSettings, TemporalSettings
from ad_detect.domain import FeatureWindow, LabelRevision, TemporalStrategy

from .replay import ReplayResult, RulesReplayEvaluator


@dataclass(frozen=True, slots=True)
class TemporalComparisonResult:
    results: Mapping[TemporalStrategy, ReplayResult]


class TemporalComparisonEvaluator:
    def __init__(
        self,
        rules: RuleSettings,
        detection: DetectionSettings,
        temporal: TemporalSettings,
        *,
        feature_schema: str,
        stale_after_ms: float,
        max_av_skew_ms: float,
    ) -> None:
        self._rules = rules
        self._detection = detection
        self._temporal = temporal
        self._feature_schema = feature_schema
        self._stale_after_ms = stale_after_ms
        self._max_av_skew_ms = max_av_skew_ms

    async def evaluate(
        self,
        windows: Sequence[FeatureWindow],
        labels: Sequence[LabelRevision],
    ) -> TemporalComparisonResult:
        results: dict[TemporalStrategy, ReplayResult] = {}
        for strategy in TemporalStrategy:
            evaluator = RulesReplayEvaluator(
                self._rules,
                self._detection,
                feature_schema=self._feature_schema,
                stale_after_ms=self._stale_after_ms,
                max_av_skew_ms=self._max_av_skew_ms,
                temporal=self._temporal,
                strategy=strategy,
            )
            results[strategy] = await evaluator.evaluate(windows, labels)
        return TemporalComparisonResult(results)
