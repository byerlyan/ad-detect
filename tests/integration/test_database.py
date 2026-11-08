import sqlite3
from pathlib import Path

from ad_detect.data import SQLiteStore
from ad_detect.domain import (
    BoundaryKind,
    Classification,
    FeatureWindow,
    feature_values,
)


def test_config_revisions_are_content_addressed_and_events_ordered() -> None:
    with SQLiteStore(":memory:") as store:
        first = store.save_config_revision({"b": 2, "a": 1}, created_at_ns=1)
        second = store.save_config_revision({"a": 1, "b": 2}, created_at_ns=2)
        assert first == second

        store.append_event(session_id="s", at_ns=2, kind="later", payload={"z": 1})
        store.append_event(session_id="s", at_ns=1, kind="earlier", payload={"z": 2})
        assert [record.kind for record in store.timeline(session_id="s")] == ["earlier", "later"]


def test_feature_windows_and_append_only_label_corrections() -> None:
    window = FeatureWindow(
        schema_version="classical-v1",
        session_id="session-a",
        start_ns=0,
        end_ns=1_000_000_000,
        values=feature_values({"audio.rms": 0.25}),
        missing=frozenset({"video"}),
    )
    with SQLiteStore(":memory:") as store:
        store.save_feature_window(window)
        original = store.add_label(
            session_id="session-a",
            start_ns=0,
            end_ns=1_000_000_000,
            label=Classification.COMMERCIAL,
            boundary=BoundaryKind.START,
            confidence=0.8,
            author="reviewer",
            created_at_ns=10,
        )
        correction = store.add_label(
            session_id="session-a",
            start_ns=0,
            end_ns=1_000_000_000,
            label=Classification.PROGRAM,
            author="reviewer",
            created_at_ns=20,
            supersedes_id=original.revision_id,
            notes="false positive",
        )

        assert store.feature_windows(session_id="session-a") == [window]
        assert store.labels(session_id="session-a") == [original, correction]
        assert store.labels(session_id="session-a", effective_only=True) == [correction]


def test_v1_database_is_migrated_without_losing_events(tmp_path: Path) -> None:
    path = str(tmp_path / "legacy.sqlite3")
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version(version) VALUES (1);
        CREATE TABLE event (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            at_ns INTEGER NOT NULL,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        INSERT INTO event(session_id, at_ns, kind, payload_json)
            VALUES ('legacy', 1, 'capture', '{}');
        """
    )
    connection.commit()
    connection.close()

    with SQLiteStore(path) as store:
        assert [event.kind for event in store.timeline(session_id="legacy")] == ["capture"]
        label = store.add_label(
            session_id="legacy",
            start_ns=0,
            end_ns=1,
            label=Classification.UNKNOWN,
            author="migration-test",
            created_at_ns=2,
        )
        assert store.labels(effective_only=True) == [label]
        assert store.fingerprints() == []
        assert store.evaluation_runs() == []
        assert store.registered_models() == []
