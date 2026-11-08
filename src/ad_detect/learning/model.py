"""Optional scikit-learn training, calibration, evaluation, and immutable bundles."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from ad_detect.data.database import canonical_json
from ad_detect.domain import Classification, FeatureWindow

from .examples import LabeledExample, SessionSplit


class ModelModality(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"
    MULTIMODAL = "multimodal"


class ModelAlgorithm(StrEnum):
    LOGISTIC_REGRESSION = "logistic_regression"
    HISTOGRAM_GRADIENT_BOOSTING = "histogram_gradient_boosting"


@dataclass(frozen=True, slots=True)
class CandidateMetrics:
    examples: int
    accuracy: float
    multiclass_brier: float
    log_loss: float
    expected_calibration_error: float
    confusion: Mapping[str, Mapping[str, int]]


@dataclass(frozen=True, slots=True)
class ModelBundleManifest:
    digest: str
    model_sha256: str
    model_filename: str
    feature_schema: str
    aggregation_schema: str
    dataset_digest: str
    modality: ModelModality
    algorithm: ModelAlgorithm
    columns: tuple[str, ...]
    classes: tuple[str, ...]
    calibration_method: str
    sklearn_version: str
    numpy_version: str
    created_at_ns: int
    metrics: Mapping[str, float | int]


@dataclass(slots=True)
class TrainedTabularModel:
    estimator: Any
    calibrator: Any | None
    columns: tuple[str, ...]
    classes: tuple[str, ...]
    modality: ModelModality
    algorithm: ModelAlgorithm
    feature_schema: str
    aggregation_schema: str
    calibration_method: str

    def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
        base = _ordered_probabilities(self.estimator, matrix, self.classes)
        if self.calibrator is None:
            return base
        calibration_input = np.log(np.clip(base, 1e-9, 1.0))
        return _ordered_probabilities(self.calibrator, calibration_input, self.classes)


def train_candidate(
    examples: Sequence[LabeledExample],
    split: SessionSplit,
    *,
    modality: ModelModality,
    algorithm: ModelAlgorithm,
) -> TrainedTabularModel:
    modules = _require_ml()
    training = [item for item in examples if item.features.session_id in split.train]
    calibration = [item for item in examples if item.features.session_id in split.calibration]
    if not training:
        raise ValueError("training split contains no examples")
    schemas = {item.features.schema_version for item in training}
    if len(schemas) != 1:
        raise ValueError("training examples require one aggregate feature schema")
    aggregation_schema = schemas.pop()
    feature_schema = aggregation_schema.split(":", maxsplit=1)[-1]
    columns = _columns(training, modality)
    if not columns:
        raise ValueError(f"no {modality.value} feature columns are available")
    classes = tuple(classification.value for classification in Classification)
    train_labels = np.asarray([item.label.value for item in training])
    if set(train_labels) != set(classes):
        raise ValueError("training split must contain PROGRAM, COMMERCIAL, and UNKNOWN")
    train_matrix = _matrix(training, columns)
    pipeline = _pipeline(modules, algorithm)
    pipeline.fit(train_matrix, train_labels)

    calibrator: Any | None = None
    calibration_method = "none-insufficient-calibration-groups"
    calibration_labels = np.asarray([item.label.value for item in calibration])
    if calibration and set(calibration_labels) == set(classes):
        base = _ordered_probabilities(pipeline, _matrix(calibration, columns), classes)
        calibrator = modules["LogisticRegression"](
            max_iter=1000,
            class_weight="balanced",
            random_state=0,
        )
        calibrator.fit(np.log(np.clip(base, 1e-9, 1.0)), calibration_labels)
        calibration_method = "held-out-session multinomial logistic"
    return TrainedTabularModel(
        estimator=pipeline,
        calibrator=calibrator,
        columns=columns,
        classes=classes,
        modality=modality,
        algorithm=algorithm,
        feature_schema=feature_schema,
        aggregation_schema=aggregation_schema,
        calibration_method=calibration_method,
    )


def evaluate_candidate(
    model: TrainedTabularModel,
    examples: Sequence[LabeledExample],
) -> CandidateMetrics:
    if not examples:
        raise ValueError("evaluation requires examples")
    matrix = _matrix(examples, model.columns)
    probabilities = model.predict_proba(matrix)
    truth = [item.label.value for item in examples]
    predicted = [model.classes[index] for index in np.argmax(probabilities, axis=1)]
    class_index = {label: index for index, label in enumerate(model.classes)}
    one_hot = np.zeros_like(probabilities)
    for row, label in enumerate(truth):
        one_hot[row, class_index[label]] = 1.0
    accuracy = sum(
        actual == estimate for actual, estimate in zip(truth, predicted, strict=True)
    ) / len(truth)
    brier = float(np.mean(np.sum(np.square(probabilities - one_hot), axis=1)))
    losses = [
        -np.log(max(probabilities[row, class_index[label]], 1e-9))
        for row, label in enumerate(truth)
    ]
    confidence = np.max(probabilities, axis=1)
    correct = np.asarray(
        [actual == estimate for actual, estimate in zip(truth, predicted, strict=True)]
    )
    confusion = {actual: {estimate: 0 for estimate in model.classes} for actual in model.classes}
    for actual, estimate in zip(truth, predicted, strict=True):
        confusion[actual][estimate] += 1
    return CandidateMetrics(
        examples=len(examples),
        accuracy=accuracy,
        multiclass_brier=brier,
        log_loss=float(np.mean(losses)),
        expected_calibration_error=_expected_calibration_error(confidence, correct),
        confusion=confusion,
    )


def save_model_bundle(
    model: TrainedTabularModel,
    output_directory: str | Path,
    *,
    dataset_digest: str,
    created_at_ns: int,
    metrics: CandidateMetrics | None = None,
) -> tuple[Path, ModelBundleManifest]:
    modules = _require_ml()
    root = Path(output_directory)
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="model-", dir=root))
    try:
        model_filename = "model.joblib"
        model_path = temporary / model_filename
        modules["joblib"].dump(model, model_path)
        model_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
        metric_summary: dict[str, float | int] = {}
        if metrics is not None:
            metric_summary = {
                "examples": metrics.examples,
                "accuracy": metrics.accuracy,
                "multiclass_brier": metrics.multiclass_brier,
                "log_loss": metrics.log_loss,
                "expected_calibration_error": metrics.expected_calibration_error,
            }
        payload: dict[str, Any] = {
            "format_version": 1,
            "model_sha256": model_sha,
            "model_filename": model_filename,
            "feature_schema": model.feature_schema,
            "aggregation_schema": model.aggregation_schema,
            "dataset_digest": dataset_digest,
            "modality": model.modality.value,
            "algorithm": model.algorithm.value,
            "columns": list(model.columns),
            "classes": list(model.classes),
            "calibration_method": model.calibration_method,
            "sklearn_version": modules["sklearn"].__version__,
            "numpy_version": np.__version__,
            "created_at_ns": created_at_ns,
            "metrics": metric_summary,
        }
        document = canonical_json(payload)
        digest = hashlib.sha256(document.encode()).hexdigest()
        (temporary / "manifest.json").write_text(document, encoding="utf-8")
        target = root / digest
        if target.exists():
            shutil.rmtree(temporary)
        else:
            os.replace(temporary, target)
        return target, _manifest(digest, payload)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def load_model_bundle(
    path: str | Path,
    *,
    expected_feature_schema: str | None = None,
) -> tuple[TrainedTabularModel, ModelBundleManifest]:
    modules = _require_ml()
    directory = Path(path)
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("model manifest must be an object")
    document = canonical_json(payload)
    digest = hashlib.sha256(document.encode()).hexdigest()
    if directory.name != digest:
        raise ValueError("model directory does not match manifest hash")
    manifest = _manifest(digest, payload)
    if manifest.sklearn_version != modules["sklearn"].__version__:
        raise ValueError("model scikit-learn version is incompatible")
    if manifest.numpy_version != np.__version__:
        raise ValueError("model NumPy version is incompatible")
    if expected_feature_schema is not None and manifest.feature_schema != expected_feature_schema:
        raise ValueError("model feature schema is incompatible")
    model_path = directory / manifest.model_filename
    if hashlib.sha256(model_path.read_bytes()).hexdigest() != manifest.model_sha256:
        raise ValueError("model artifact hash does not match manifest")
    model = modules["joblib"].load(model_path)
    if not isinstance(model, TrainedTabularModel):
        raise ValueError("model artifact has an unexpected type")
    if model.columns != manifest.columns or model.classes != manifest.classes:
        raise ValueError("model preprocessing contract does not match manifest")
    if model.feature_schema != manifest.feature_schema:
        raise ValueError("model feature schema does not match manifest")
    return model, manifest


def _require_ml() -> dict[str, Any]:
    os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 1))
    try:
        sklearn = importlib.import_module("sklearn")
        joblib = importlib.import_module("joblib")
        ensemble = importlib.import_module("sklearn.ensemble")
        impute = importlib.import_module("sklearn.impute")
        linear = importlib.import_module("sklearn.linear_model")
        pipeline = importlib.import_module("sklearn.pipeline")
        preprocessing = importlib.import_module("sklearn.preprocessing")
    except ImportError as exc:
        raise RuntimeError(
            "Phase 5 ML dependencies are unavailable; install with `pip install -e '.[ml]'`"
        ) from exc
    return {
        "sklearn": sklearn,
        "joblib": joblib,
        "HistGradientBoostingClassifier": ensemble.HistGradientBoostingClassifier,
        "SimpleImputer": impute.SimpleImputer,
        "LogisticRegression": linear.LogisticRegression,
        "Pipeline": pipeline.Pipeline,
        "StandardScaler": preprocessing.StandardScaler,
    }


def _pipeline(modules: Mapping[str, Any], algorithm: ModelAlgorithm) -> Any:
    steps: list[tuple[str, Any]] = [
        ("imputer", modules["SimpleImputer"](strategy="median", add_indicator=True))
    ]
    if algorithm is ModelAlgorithm.LOGISTIC_REGRESSION:
        steps.extend(
            (
                ("scaler", modules["StandardScaler"]()),
                (
                    "classifier",
                    modules["LogisticRegression"](
                        max_iter=1000,
                        class_weight="balanced",
                        random_state=0,
                    ),
                ),
            )
        )
    else:
        steps.append(
            (
                "classifier",
                modules["HistGradientBoostingClassifier"](
                    max_iter=200,
                    learning_rate=0.08,
                    random_state=0,
                ),
            )
        )
    return modules["Pipeline"](steps)


def _columns(examples: Sequence[LabeledExample], modality: ModelModality) -> tuple[str, ...]:
    keys = {key for item in examples for key in item.features.values}
    if modality is ModelModality.MULTIMODAL:
        return tuple(sorted(keys))
    marker = f".{modality.value}."
    shared = (".health.", ".missing.", ".meta.")
    return tuple(
        sorted(key for key in keys if marker in key or any(item in key for item in shared))
    )


def _matrix(examples: Sequence[LabeledExample], columns: Sequence[str]) -> np.ndarray:
    return np.asarray(
        [[item.features.values.get(column, np.nan) for column in columns] for item in examples],
        dtype=np.float64,
    )


def feature_matrix(windows: Sequence[FeatureWindow], columns: Sequence[str]) -> np.ndarray:
    return np.asarray(
        [[window.values.get(column, np.nan) for column in columns] for window in windows],
        dtype=np.float64,
    )


def _ordered_probabilities(
    estimator: Any, matrix: np.ndarray, classes: Sequence[str]
) -> np.ndarray:
    raw = np.asarray(estimator.predict_proba(matrix), dtype=np.float64)
    estimator_classes = [str(item) for item in estimator.classes_]
    result = np.zeros((matrix.shape[0], len(classes)), dtype=np.float64)
    for target_index, label in enumerate(classes):
        if label in estimator_classes:
            result[:, target_index] = raw[:, estimator_classes.index(label)]
    totals = result.sum(axis=1, keepdims=True)
    normalized = np.divide(result, totals, out=np.zeros_like(result), where=totals > 0)
    return np.asarray(normalized, dtype=np.float64)


def _expected_calibration_error(
    confidence: np.ndarray,
    correct: np.ndarray,
    *,
    bins: int = 10,
) -> float:
    total = confidence.size
    error = 0.0
    for lower in np.linspace(0.0, 1.0, bins, endpoint=False):
        upper = lower + 1.0 / bins
        selected = (confidence >= lower) & (
            confidence <= upper if upper >= 1.0 else confidence < upper
        )
        if np.any(selected):
            error += float(np.sum(selected) / total) * abs(
                float(np.mean(confidence[selected])) - float(np.mean(correct[selected]))
            )
    return error


def _manifest(digest: str, payload: Mapping[str, Any]) -> ModelBundleManifest:
    return ModelBundleManifest(
        digest=digest,
        model_sha256=str(payload["model_sha256"]),
        model_filename=str(payload["model_filename"]),
        feature_schema=str(payload["feature_schema"]),
        aggregation_schema=str(payload["aggregation_schema"]),
        dataset_digest=str(payload["dataset_digest"]),
        modality=ModelModality(str(payload["modality"])),
        algorithm=ModelAlgorithm(str(payload["algorithm"])),
        columns=tuple(str(item) for item in payload["columns"]),
        classes=tuple(str(item) for item in payload["classes"]),
        calibration_method=str(payload["calibration_method"]),
        sklearn_version=str(payload["sklearn_version"]),
        numpy_version=str(payload["numpy_version"]),
        created_at_ns=int(payload["created_at_ns"]),
        metrics={str(key): value for key, value in payload.get("metrics", {}).items()},
    )
