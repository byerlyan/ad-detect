import json
from pathlib import Path

import pytest

from ad_detect.config import load_settings
from ad_detect.data import EvaluationRunRecord, SQLiteStore
from ad_detect.domain import Classification, FeatureWindow, feature_values
from ad_detect.learning import (
    ACTIVATE_MODEL,
    REGISTER_MODEL,
    AuthorizedActor,
    ModelAlgorithm,
    ModelModality,
    ModelRegistry,
    export_dataset,
    run_evaluation,
    save_model_bundle,
    train_candidate,
)
from ad_detect.learning.examples import LabeledExample, SessionSplit

SECOND = 1_000_000_000


def _dataset(tmp_path: Path) -> Path:
    with SQLiteStore(":memory:") as source:
        for session_index in range(5):
            session_id = f"session-{session_index}"
            for index, signal in enumerate((0.0, 0.9, 0.9, 0.0)):
                source.save_feature_window(
                    FeatureWindow(
                        schema_version="classical-v1",
                        session_id=session_id,
                        start_ns=index * SECOND,
                        end_ns=(index + 1) * SECOND,
                        values=feature_values(
                            {
                                "audio.rms": signal,
                                "audio.spectral_flux": signal,
                                "video.scene_cut_flag.mean": signal,
                                "video.edge_density.mean": signal,
                                "video.colorfulness.mean": signal,
                            }
                        ),
                    )
                )
            source.add_label(
                session_id=session_id,
                start_ns=0,
                end_ns=SECOND,
                label=Classification.PROGRAM,
                author="fixture",
                created_at_ns=1,
            )
            source.add_label(
                session_id=session_id,
                start_ns=SECOND,
                end_ns=3 * SECOND,
                label=Classification.COMMERCIAL,
                author="fixture",
                created_at_ns=2,
            )
            source.add_label(
                session_id=session_id,
                start_ns=3 * SECOND,
                end_ns=4 * SECOND,
                label=Classification.UNKNOWN,
                author="fixture",
                created_at_ns=3,
            )
        return export_dataset(source, tmp_path, created_at_ns=10).path


@pytest.mark.asyncio
async def test_evaluation_run_is_content_addressed_and_locks_final_test(tmp_path: Path) -> None:
    dataset_path = _dataset(tmp_path / "datasets")
    settings = load_settings()
    model = train_candidate(
        _model_examples(),
        SessionSplit(
            train=frozenset({"session-0", "session-1", "session-2"}),
            calibration=frozenset({"session-3"}),
            test=frozenset({"session-4", "session-5"}),
        ),
        modality=ModelModality.MULTIMODAL,
        algorithm=ModelAlgorithm.LOGISTIC_REGRESSION,
    )
    candidate_path, candidate = save_model_bundle(
        model,
        tmp_path / "models",
        dataset_digest=dataset_path.stem,
        created_at_ns=20,
    )
    with SQLiteStore(":memory:") as store:
        result = await run_evaluation(
            store,
            settings,
            dataset_path,
            [candidate_path],
            tmp_path / "reports",
            split_seed="fixed-seed",
            source_revision="abc123",
            source_dirty=True,
            created_at_ns=100,
        )

        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        report = json.loads(result.report_path.read_text(encoding="utf-8"))
        assert result.directory.name == result.record.digest
        assert manifest["report_digest"] == result.record.report_digest
        assert manifest["split"]["final_test_locked"] is True
        assert set(report["temporal_rules_matrix"]) == {
            "hysteresis",
            "sliding_window",
            "hmm",
        }
        assert report["ml_candidates"][0]["bundle_digest"] == candidate.digest
        assert report["accuracy_claim"] is None
        assert store.evaluation_run(result.record.digest) == result.record
        html = result.html_path.read_text(encoding="utf-8")
        assert "No accuracy claim" in html
        assert "<script" not in html

        with pytest.raises(ValueError, match="locked differently"):
            store.lock_test_partition(
                result.record.dataset_digest,
                {"different-session"},
                grouping_digest="different",
                created_at_ns=200,
            )


def _model_examples() -> list[LabeledExample]:
    result: list[LabeledExample] = []
    values = {
        Classification.PROGRAM: (0.1, 10.0),
        Classification.COMMERCIAL: (0.9, 90.0),
        Classification.UNKNOWN: (0.5, 50.0),
    }
    for session_index in range(6):
        for classification, (audio, video) in values.items():
            result.append(
                LabeledExample(
                    features=FeatureWindow(
                        schema_version="aggregate-v1:classical-v1",
                        session_id=f"session-{session_index}",
                        start_ns=0,
                        end_ns=1,
                        values=feature_values(
                            {
                                "w3.audio.rms.mean": audio,
                                "w3.video.luma_mean.mean": video,
                                "w3.meta.coverage": 1.0,
                            }
                        ),
                    ),
                    label=classification,
                    label_revision_ids=(classification.value,),
                    labeled_ratio=1.0,
                )
            )
    return result


def test_registry_never_activates_on_register_and_supports_audited_rollback(
    tmp_path: Path,
) -> None:
    examples = _model_examples()
    split = SessionSplit(
        train=frozenset({"session-0", "session-1", "session-2"}),
        calibration=frozenset({"session-3"}),
        test=frozenset({"session-4", "session-5"}),
    )
    paths: list[Path] = []
    manifests = []
    for algorithm in ModelAlgorithm:
        model = train_candidate(
            examples,
            split,
            modality=ModelModality.MULTIMODAL,
            algorithm=algorithm,
        )
        path, manifest = save_model_bundle(
            model,
            tmp_path / "models",
            dataset_digest="d" * 64,
            created_at_ns=10,
        )
        paths.append(path)
        manifests.append(manifest)

    evaluation = EvaluationRunRecord(
        digest="e" * 64,
        dataset_digest="d" * 64,
        report_digest="r" * 64,
        report_path="reports/report.json",
        status="complete",
        payload={"model_digests": [manifest.digest for manifest in manifests]},
        created_at_ns=1,
        completed_at_ns=2,
    )
    register_actor = AuthorizedActor("trainer", frozenset({REGISTER_MODEL}))
    activate_actor = AuthorizedActor("operator", frozenset({ACTIVATE_MODEL}))
    with SQLiteStore(":memory:") as store:
        store.save_evaluation_run(evaluation)
        registry = ModelRegistry(store, feature_schema="classical-v1")
        first = registry.register(
            paths[0],
            evaluation.digest,
            authorization=register_actor,
            registered_at_ns=20,
        )
        second = registry.register(
            paths[1],
            evaluation.digest,
            authorization=register_actor,
            registered_at_ns=21,
        )

        assert not first.active and not second.active
        assert not any(model.active for model in registry.models())
        with pytest.raises(PermissionError):
            registry.activate(
                first.model_id,
                authorization=register_actor,
                reason="not authorized",
                config_digest="c" * 64,
                at_ns=30,
            )

        first_activation = registry.activate(
            first.model_id,
            authorization=activate_actor,
            reason="reviewed held-out report",
            config_digest="c" * 64,
            at_ns=31,
        )
        second_activation = registry.activate(
            second.model_id,
            authorization=activate_actor,
            reason="controlled comparison",
            config_digest="c" * 64,
            at_ns=32,
        )
        rollback = registry.rollback(
            second_activation.sequence,
            authorization=activate_actor,
            reason="restore previous model",
            config_digest="c" * 64,
            at_ns=33,
        )

        assert first_activation.previous_model_id is None
        assert rollback.action == "rollback"
        assert rollback.requested_model_id == first.model_id
        assert store.active_model() is not None
        assert store.active_model().model_id == first.model_id
        assert [model.model_id for model in registry.models() if model.active] == [first.model_id]
        assert len(store.model_activations()) == 3
        registry.register(
            paths[0],
            evaluation.digest,
            authorization=register_actor,
            registered_at_ns=20,
        )
        artifact = paths[1] / manifests[1].model_filename
        artifact.write_bytes(artifact.read_bytes() + b"tampered")
        with pytest.raises(ValueError, match="artifact hash"):
            registry.activate(
                second.model_id,
                authorization=activate_actor,
                reason="must fail closed",
                config_digest="c" * 64,
                at_ns=34,
            )
        assert store.model_activations()[-1].outcome == "failed"
        assert [model.model_id for model in registry.models() if model.active] == [first.model_id]
