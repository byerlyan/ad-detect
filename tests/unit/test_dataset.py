from pathlib import Path

import pytest

from ad_detect.data import SQLiteStore
from ad_detect.domain import Classification, FeatureWindow, feature_values
from ad_detect.learning import export_dataset, import_dataset, load_dataset_manifest


def test_dataset_export_is_deterministic_and_hash_verified(tmp_path: Path) -> None:
    with SQLiteStore(":memory:") as store:
        store.append_event(
            session_id="s",
            at_ns=0,
            kind="session.started",
            payload={"config_digest": "original", "video_device": "video:1:test"},
        )
        store.save_feature_window(
            FeatureWindow(
                schema_version="classical-v1",
                session_id="s",
                start_ns=0,
                end_ns=1,
                values=feature_values({"audio.rms": 0.2}),
            )
        )
        original = store.add_label(
            session_id="s",
            start_ns=1,
            end_ns=2,
            label=Classification.COMMERCIAL,
            author="reviewer",
            created_at_ns=1,
        )
        store.add_label(
            session_id="s",
            start_ns=0,
            end_ns=1,
            label=Classification.PROGRAM,
            author="reviewer",
            created_at_ns=2,
            supersedes_id=original.revision_id,
        )

        first = export_dataset(
            store, tmp_path, created_at_ns=5, provenance={"config_digest": "abc"}
        )
        second = export_dataset(
            store, tmp_path, created_at_ns=5, provenance={"config_digest": "abc"}
        )

        assert first.digest == second.digest
        assert load_dataset_manifest(first.path).payload == first.payload
        assert first.payload["provenance"] == {"config_digest": "abc"}
        assert first.payload["session_metadata"] == [
            {
                "session_id": "s",
                "started": {
                    "config_digest": "original",
                    "video_device": "video:1:test",
                },
            }
        ]

        with SQLiteStore(":memory:") as imported:
            import_dataset(imported, first.path)
            assert imported.feature_windows() == store.feature_windows()
            assert imported.labels() == store.labels()

        first.path.write_text(first.path.read_text() + " ", encoding="utf-8")
        assert load_dataset_manifest(first.path).digest == first.digest
        first.path.write_text('{"tampered":true}', encoding="utf-8")
        with pytest.raises(ValueError, match="content hash"):
            load_dataset_manifest(first.path)
