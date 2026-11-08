from fastapi.testclient import TestClient

from ad_detect.api import create_app
from ad_detect.config import load_settings
from ad_detect.data import EvaluationRunRecord, RegisteredModelRecord, SQLiteStore
from ad_detect.domain import Classification, FeatureWindow, feature_values


def test_health_state_and_dashboard() -> None:
    store = SQLiteStore(":memory:")
    store.save_feature_window(
        FeatureWindow(
            schema_version="classical-v1",
            session_id="api-test",
            start_ns=0,
            end_ns=1,
            values=feature_values({"audio.rms": 0.2}),
        )
    )
    store.add_label(
        session_id="api-test",
        start_ns=0,
        end_ns=1,
        label=Classification.COMMERCIAL,
        author="api-fixture",
        created_at_ns=1,
    )
    evaluation = EvaluationRunRecord(
        digest="evaluation",
        dataset_digest="dataset",
        report_digest="report",
        report_path="/secret/local/report.json",
        status="complete",
        payload={"model_digests": ["model"]},
        created_at_ns=1,
        completed_at_ns=2,
    )
    store.save_evaluation_run(evaluation)
    store.register_model(
        RegisteredModelRecord(
            model_id="model",
            bundle_path="/secret/local/model",
            dataset_digest="dataset",
            evaluation_digest="evaluation",
            feature_schema="classical-v1",
            aggregation_schema="aggregate-v1:classical-v1",
            modality="multimodal",
            algorithm="logistic_regression",
            registered_by="fixture",
            registered_at_ns=3,
            notes="private note",
        )
    )
    app = create_app(load_settings(), store=store, session_id="api-test")
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        state = client.get("/api/v1/state")
        assert state.status_code == 200
        assert state.json()["session_id"] == "api-test"
        assert client.get("/api/v1/events?limit=10").status_code == 200
        assert client.get("/api/v1/features").json()[0]["values"]["audio.rms"] == 0.2
        assert client.get("/api/v1/labels").json()[0]["label"] == "commercial"
        assert client.get("/api/v1/fingerprints").json() == []
        evaluations = client.get("/api/v1/evaluations").json()
        models = client.get("/api/v1/models").json()
        assert evaluations[0]["digest"] == "evaluation"
        assert "report_path" not in evaluations[0]
        assert models[0]["model_id"] == "model"
        assert "bundle_path" not in models[0]
        assert "registered_by" not in models[0]
    store.close()
