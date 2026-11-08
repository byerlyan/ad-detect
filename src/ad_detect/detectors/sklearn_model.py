"""Deadline-bounded detector for immutable tabular model bundles."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np

from ad_detect.domain import Classification, DetectorEvidence, FeatureWindow
from ad_detect.domain.models import probabilities


class TabularInferenceModel(Protocol):
    aggregation_schema: str
    columns: tuple[str, ...]
    classes: tuple[str, ...]
    modality: Any
    algorithm: Any
    calibration_method: str

    def predict_proba(self, matrix: np.ndarray) -> np.ndarray: ...


def _feature_matrix(windows: Sequence[FeatureWindow], columns: Sequence[str]) -> np.ndarray:
    return np.asarray(
        [[window.values.get(column, np.nan) for column in columns] for window in windows],
        dtype=np.float64,
    )


class SklearnModelDetector:
    def __init__(
        self,
        model: TabularInferenceModel,
        *,
        model_digest: str,
        inference_timeout_ms: int,
    ) -> None:
        if inference_timeout_ms <= 0:
            raise ValueError("inference timeout must be positive")
        self.model = model
        self.model_digest = model_digest
        self.inference_timeout_ms = inference_timeout_ms

    async def infer(self, features: FeatureWindow) -> DetectorEvidence:
        if features.schema_version != self.model.aggregation_schema:
            return self._unknown(features, "aggregate feature schema is incompatible")
        if "capture" in features.missing:
            return self._unknown(features, "capture health is invalid")
        started = time.perf_counter()
        try:
            matrix = _feature_matrix((features,), self.model.columns)
            output = await asyncio.wait_for(
                asyncio.to_thread(self.model.predict_proba, matrix),
                timeout=self.inference_timeout_ms / 1000,
            )
            row = output[0]
            by_class = {
                Classification(label): float(row[index])
                for index, label in enumerate(self.model.classes)
            }
            result_probabilities = probabilities(
                by_class[Classification.PROGRAM],
                by_class[Classification.COMMERCIAL],
                by_class[Classification.UNKNOWN],
            )
        except TimeoutError:
            return self._unknown(features, "model inference deadline exceeded")
        except Exception as exc:
            return self._unknown(features, f"model inference failed: {exc}")
        latency_ms = (time.perf_counter() - started) * 1000
        dominant = max(result_probabilities, key=result_probabilities.__getitem__)
        return DetectorEvidence(
            detector_id=f"ml-{self.model.modality.value}",
            detector_version=self.model_digest,
            window_start_ns=features.start_ns,
            window_end_ns=features.end_ns,
            probabilities=result_probabilities,
            valid=True,
            latency_ms=latency_ms,
            explanation=(
                f"{self.model.algorithm.value} predicts {dominant.value}",
                f"calibration={self.model.calibration_method}",
            ),
            metadata={
                "model_digest": self.model_digest,
                "modality": self.model.modality.value,
                "algorithm": self.model.algorithm.value,
            },
        )

    def _unknown(self, features: FeatureWindow, reason: str) -> DetectorEvidence:
        return DetectorEvidence(
            detector_id=f"ml-{self.model.modality.value}",
            detector_version=self.model_digest,
            window_start_ns=features.start_ns,
            window_end_ns=features.end_ns,
            probabilities=probabilities(0.0, 0.0, 1.0),
            valid=False,
            explanation=(reason,),
            metadata={"model_digest": self.model_digest},
        )
