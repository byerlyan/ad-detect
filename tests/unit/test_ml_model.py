import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ad_detect.detectors import SklearnModelDetector
from ad_detect.domain import Classification, FeatureWindow, feature_values
from ad_detect.learning.examples import LabeledExample, SessionSplit
from ad_detect.learning.model import (
    ModelAlgorithm,
    ModelModality,
    evaluate_candidate,
    load_model_bundle,
    save_model_bundle,
    train_candidate,
)


def _examples() -> list[LabeledExample]:
    random = np.random.default_rng(7)
    result: list[LabeledExample] = []
    centers = {
        Classification.PROGRAM: (0.1, 10.0, 0.0),
        Classification.COMMERCIAL: (0.9, 90.0, 0.0),
        Classification.UNKNOWN: (0.5, 50.0, 1.0),
    }
    for session_index in range(6):
        for classification, (audio, video, missing) in centers.items():
            for repetition in range(5):
                noise = float(random.normal(0, 0.01))
                window = FeatureWindow(
                    schema_version="aggregate-v1:classical-v1",
                    session_id=f"session-{session_index}",
                    start_ns=repetition,
                    end_ns=repetition + 1,
                    values=feature_values(
                        {
                            "w3.audio.rms.mean": audio + noise,
                            "w3.video.luma_mean.mean": video + noise,
                            "w3.missing.capture_ratio": missing,
                            "w3.meta.coverage": 1.0,
                        }
                    ),
                    missing=frozenset({"capture"})
                    if classification is Classification.UNKNOWN
                    else frozenset(),
                )
                result.append(
                    LabeledExample(
                        features=window,
                        label=classification,
                        label_revision_ids=(f"label-{classification.value}",),
                        labeled_ratio=1.0,
                    )
                )
    return result


def _split() -> SessionSplit:
    return SessionSplit(
        train=frozenset({"session-0", "session-1", "session-2"}),
        calibration=frozenset({"session-3"}),
        test=frozenset({"session-4", "session-5"}),
    )


def test_train_calibrate_evaluate_and_hash_verify_bundle(tmp_path: Path) -> None:
    examples = _examples()
    split = _split()
    model = train_candidate(
        examples,
        split,
        modality=ModelModality.MULTIMODAL,
        algorithm=ModelAlgorithm.LOGISTIC_REGRESSION,
    )
    held_out = [item for item in examples if item.features.session_id in split.test]
    metrics = evaluate_candidate(model, held_out)

    assert metrics.examples == 30
    assert metrics.accuracy > 0.9
    assert "held-out-session" in model.calibration_method

    path, manifest = save_model_bundle(
        model,
        tmp_path,
        dataset_digest="d" * 64,
        created_at_ns=123,
        metrics=metrics,
    )
    loaded, loaded_manifest = load_model_bundle(path, expected_feature_schema="classical-v1")
    assert loaded_manifest == manifest
    assert np.allclose(
        loaded.predict_proba(np.asarray([[0.1, 0.0, 1.0, 10.0]])),
        model.predict_proba(np.asarray([[0.1, 0.0, 1.0, 10.0]])),
    )

    with pytest.raises(ValueError, match="incompatible"):
        load_model_bundle(path, expected_feature_schema="different-v1")
    model_path = path / manifest.model_filename
    model_path.write_bytes(model_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="artifact hash"):
        load_model_bundle(path)


def test_histogram_candidate_emits_all_three_probabilities() -> None:
    examples = _examples()
    model = train_candidate(
        examples,
        _split(),
        modality=ModelModality.AUDIO,
        algorithm=ModelAlgorithm.HISTOGRAM_GRADIENT_BOOSTING,
    )
    held_out = [item for item in examples if item.features.session_id == "session-4"]

    probabilities = model.predict_proba(
        np.asarray(
            [
                [item.features.values.get(column, np.nan) for column in model.columns]
                for item in held_out
            ]
        )
    )

    assert probabilities.shape == (15, 3)
    assert np.allclose(probabilities.sum(axis=1), 1.0)


@pytest.mark.parametrize("modality", list(ModelModality))
@pytest.mark.parametrize("algorithm", list(ModelAlgorithm))
def test_every_declared_candidate_trains_and_emits_three_classes(
    modality: ModelModality,
    algorithm: ModelAlgorithm,
) -> None:
    examples = _examples()
    model = train_candidate(
        examples,
        _split(),
        modality=modality,
        algorithm=algorithm,
    )
    held_out = [item for item in examples if item.features.session_id == "session-4"]
    metrics = evaluate_candidate(model, held_out)

    assert model.classes == tuple(item.value for item in Classification)
    assert metrics.examples == 15
    assert set(metrics.confusion) == set(model.classes)


@pytest.mark.asyncio
async def test_model_detector_hard_gates_capture_and_schema() -> None:
    examples = _examples()
    model = train_candidate(
        examples,
        _split(),
        modality=ModelModality.VIDEO,
        algorithm=ModelAlgorithm.LOGISTIC_REGRESSION,
    )
    detector = SklearnModelDetector(model, model_digest="abc", inference_timeout_ms=200)
    healthy = next(
        item.features
        for item in examples
        if item.label is Classification.COMMERCIAL and item.features.session_id == "session-4"
    )

    evidence = await detector.infer(healthy)
    incompatible = await detector.infer(
        FeatureWindow(
            schema_version="other-v1",
            session_id="s",
            start_ns=0,
            end_ns=1,
            values=feature_values({}),
        )
    )
    invalid_capture = await detector.infer(
        FeatureWindow(
            schema_version=model.aggregation_schema,
            session_id="s",
            start_ns=0,
            end_ns=1,
            values=healthy.values,
            missing=frozenset({"capture"}),
        )
    )

    assert evidence.valid
    assert evidence.probabilities[Classification.COMMERCIAL] > 0.5
    assert not incompatible.valid
    assert not invalid_capture.valid
    assert invalid_capture.probabilities[Classification.COMMERCIAL] == 0.0


@pytest.mark.asyncio
async def test_model_detector_turns_a_deadline_overrun_into_unknown() -> None:
    class SlowModel:
        aggregation_schema = "aggregate-v1:classical-v1"
        columns = ("w1.audio.rms.mean",)
        classes = tuple(item.value for item in Classification)
        modality = SimpleNamespace(value="audio")
        algorithm = SimpleNamespace(value="slow-test")
        calibration_method = "none"

        def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
            del matrix
            time.sleep(0.3)
            return np.asarray([[0.0, 1.0, 0.0]])

    detector = SklearnModelDetector(
        SlowModel(),
        model_digest="slow",
        inference_timeout_ms=10,
    )
    features = FeatureWindow(
        schema_version="aggregate-v1:classical-v1",
        session_id="s",
        start_ns=0,
        end_ns=1,
        values=feature_values({"w1.audio.rms.mean": 0.5}),
    )

    started = asyncio.get_running_loop().time()
    evidence = await detector.infer(features)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.2
    assert not evidence.valid
    assert evidence.probabilities[Classification.COMMERCIAL] == 0.0
    assert "deadline exceeded" in evidence.explanation[0]
