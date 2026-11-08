from fastapi.testclient import TestClient

from ad_detect.api import create_app
from ad_detect.config import load_settings
from ad_detect.data import SQLiteStore
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
    store.close()
