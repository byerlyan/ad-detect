"""Small SQLite repository with explicit schema migrations and canonical JSON."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from ad_detect.domain import (
    AudioLandmark,
    BoundaryKind,
    Classification,
    FeatureWindow,
    FingerprintHashHit,
    FingerprintReference,
    FingerprintStatus,
    LabelRevision,
    SetupStep,
    SetupStepResult,
    SetupStepStatus,
    feature_values,
)

SCHEMA_VERSION = 6


@dataclass(frozen=True, slots=True)
class EventRecord:
    sequence: int
    session_id: str
    at_ns: int
    kind: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    digest: str
    relative_path: str
    size_bytes: int
    media_type: str
    protected: bool
    created_at_ns: int
    session_id: str | None = None
    start_ns: int | None = None
    end_ns: int | None = None

    def __post_init__(self) -> None:
        span = (self.session_id, self.start_ns, self.end_ns)
        if any(value is not None for value in span) and any(value is None for value in span):
            raise ValueError("artifact session span must be complete")
        if self.start_ns is not None and self.end_ns is not None and self.end_ns <= self.start_ns:
            raise ValueError("artifact end must follow start")


@dataclass(frozen=True, slots=True)
class EvaluationRunRecord:
    digest: str
    dataset_digest: str
    report_digest: str
    report_path: str
    status: str
    payload: Mapping[str, Any]
    created_at_ns: int
    completed_at_ns: int


@dataclass(frozen=True, slots=True)
class RegisteredModelRecord:
    model_id: str
    bundle_path: str
    dataset_digest: str
    evaluation_digest: str
    feature_schema: str
    aggregation_schema: str
    modality: str
    algorithm: str
    registered_by: str
    registered_at_ns: int
    notes: str
    active: bool = False


@dataclass(frozen=True, slots=True)
class ModelActivationRecord:
    sequence: int
    action: str
    requested_model_id: str
    previous_model_id: str | None
    actor: str
    reason: str
    config_digest: str
    at_ns: int
    outcome: str
    explanation: str


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA foreign_keys = ON")
            if self._path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = self._connection.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self._connection.execute("INSERT INTO schema_version(version) VALUES (0)")
                version = 0
            else:
                version = int(row["version"])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS event (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    at_ns INTEGER NOT NULL CHECK(at_ns >= 0),
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS event_session_time "
                "ON event(session_id, at_ns, sequence)"
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS config_revision (
                    digest TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at_ns INTEGER NOT NULL CHECK(created_at_ns >= 0)
                )
                """
            )
            if version < 1:
                version = 1
                self._connection.execute("UPDATE schema_version SET version = 1")
            if version < 2:
                self._migrate_v2()
                version = 2
                self._connection.execute("UPDATE schema_version SET version = 2")
            if version < 3:
                self._migrate_v3()
                version = 3
                self._connection.execute("UPDATE schema_version SET version = 3")
            if version < 4:
                self._migrate_v4()
                version = 4
                self._connection.execute("UPDATE schema_version SET version = 4")
            if version < 5:
                self._migrate_v5()
                version = 5
                self._connection.execute("UPDATE schema_version SET version = 5")
            if version < 6:
                self._migrate_v6()
                self._connection.execute("UPDATE schema_version SET version = 6")

    def _migrate_v2(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS feature_window (
                session_id TEXT NOT NULL,
                start_ns INTEGER NOT NULL,
                end_ns INTEGER NOT NULL,
                schema_version TEXT NOT NULL,
                values_json TEXT NOT NULL,
                missing_json TEXT NOT NULL,
                PRIMARY KEY(session_id, start_ns, end_ns, schema_version),
                CHECK(end_ns > start_ns)
            );
            CREATE TABLE IF NOT EXISTS label_revision (
                revision_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                start_ns INTEGER NOT NULL,
                end_ns INTEGER NOT NULL,
                label TEXT NOT NULL CHECK(label IN ('program', 'commercial', 'unknown')),
                boundary TEXT NOT NULL CHECK(boundary IN ('none', 'start', 'end')),
                confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                author TEXT NOT NULL,
                created_at_ns INTEGER NOT NULL,
                supersedes_id TEXT REFERENCES label_revision(revision_id),
                notes TEXT NOT NULL,
                CHECK(end_ns > start_ns)
            );
            CREATE INDEX IF NOT EXISTS label_session_span
                ON label_revision(session_id, start_ns, end_ns, created_at_ns);
            CREATE TABLE IF NOT EXISTS artifact (
                digest TEXT PRIMARY KEY,
                relative_path TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                media_type TEXT NOT NULL,
                protected INTEGER NOT NULL CHECK(protected IN (0, 1)),
                created_at_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dataset_manifest (
                digest TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at_ns INTEGER NOT NULL
            );
            """
        )

    def _migrate_v3(self) -> None:
        columns = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(artifact)").fetchall()
        }
        if "session_id" not in columns:
            self._connection.execute("ALTER TABLE artifact ADD COLUMN session_id TEXT")
        if "start_ns" not in columns:
            self._connection.execute("ALTER TABLE artifact ADD COLUMN start_ns INTEGER")
        if "end_ns" not in columns:
            self._connection.execute("ALTER TABLE artifact ADD COLUMN end_ns INTEGER")
        self._connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS artifact_session_span
                ON artifact(session_id, start_ns, end_ns);
            CREATE TABLE IF NOT EXISTS artifact_span (
                digest TEXT NOT NULL REFERENCES artifact(digest) ON DELETE CASCADE,
                session_id TEXT NOT NULL,
                start_ns INTEGER NOT NULL,
                end_ns INTEGER NOT NULL,
                PRIMARY KEY(digest, session_id, start_ns, end_ns),
                CHECK(end_ns > start_ns)
            );
            CREATE INDEX IF NOT EXISTS artifact_span_session_time
                ON artifact_span(session_id, start_ns, end_ns);
            """
        )

    def _migrate_v4(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS fingerprint_ad (
                ad_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                label_revision_id TEXT NOT NULL REFERENCES label_revision(revision_id),
                session_id TEXT NOT NULL,
                start_ns INTEGER NOT NULL,
                end_ns INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL CHECK(duration_ms > 0),
                fingerprint_version TEXT NOT NULL,
                landmark_count INTEGER NOT NULL CHECK(landmark_count > 0),
                status TEXT NOT NULL CHECK(status IN ('active', 'disabled')),
                created_at_ns INTEGER NOT NULL,
                CHECK(end_ns > start_ns)
            );
            CREATE INDEX IF NOT EXISTS fingerprint_ad_status
                ON fingerprint_ad(status, created_at_ns);
            CREATE TABLE IF NOT EXISTS fingerprint_hash (
                ad_id TEXT NOT NULL REFERENCES fingerprint_ad(ad_id) ON DELETE CASCADE,
                hash_value INTEGER NOT NULL,
                anchor_ms INTEGER NOT NULL CHECK(anchor_ms >= 0),
                PRIMARY KEY(ad_id, hash_value, anchor_ms)
            );
            CREATE INDEX IF NOT EXISTS fingerprint_hash_lookup
                ON fingerprint_hash(hash_value, ad_id, anchor_ms);
            """
        )

    def _migrate_v5(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS test_partition_lock (
                dataset_digest TEXT PRIMARY KEY,
                sessions_json TEXT NOT NULL,
                grouping_digest TEXT NOT NULL,
                created_at_ns INTEGER NOT NULL CHECK(created_at_ns >= 0)
            );
            CREATE TABLE IF NOT EXISTS evaluation_run (
                digest TEXT PRIMARY KEY,
                dataset_digest TEXT NOT NULL,
                report_digest TEXT NOT NULL,
                report_path TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('complete', 'failed')),
                payload_json TEXT NOT NULL,
                created_at_ns INTEGER NOT NULL CHECK(created_at_ns >= 0),
                completed_at_ns INTEGER NOT NULL CHECK(completed_at_ns >= created_at_ns)
            );
            CREATE INDEX IF NOT EXISTS evaluation_dataset_time
                ON evaluation_run(dataset_digest, created_at_ns);
            CREATE TABLE IF NOT EXISTS registered_model (
                model_id TEXT PRIMARY KEY,
                bundle_path TEXT NOT NULL,
                dataset_digest TEXT NOT NULL,
                evaluation_digest TEXT NOT NULL REFERENCES evaluation_run(digest),
                feature_schema TEXT NOT NULL,
                aggregation_schema TEXT NOT NULL,
                modality TEXT NOT NULL,
                algorithm TEXT NOT NULL,
                registered_by TEXT NOT NULL,
                registered_at_ns INTEGER NOT NULL CHECK(registered_at_ns >= 0),
                notes TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_activation (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL CHECK(action IN ('activate', 'rollback')),
                requested_model_id TEXT NOT NULL REFERENCES registered_model(model_id),
                previous_model_id TEXT REFERENCES registered_model(model_id),
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                config_digest TEXT NOT NULL,
                at_ns INTEGER NOT NULL CHECK(at_ns >= 0),
                outcome TEXT NOT NULL CHECK(outcome IN ('succeeded', 'failed')),
                explanation TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS active_model (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                model_id TEXT NOT NULL REFERENCES registered_model(model_id),
                activation_sequence INTEGER NOT NULL REFERENCES model_activation(sequence)
            );
            """
        )

    def _migrate_v6(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS active_config (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                digest TEXT NOT NULL REFERENCES config_revision(digest),
                activated_at_ns INTEGER NOT NULL CHECK(activated_at_ns >= 0)
            );
            CREATE TABLE IF NOT EXISTS setup_step_result (
                step TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(status IN ('complete', 'skipped')),
                config_digest TEXT NOT NULL REFERENCES config_revision(digest),
                payload_json TEXT NOT NULL,
                actor TEXT NOT NULL,
                completed_at_ns INTEGER NOT NULL CHECK(completed_at_ns >= 0)
            );
            CREATE TABLE IF NOT EXISTS setup_transition (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                step TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('complete', 'skip', 'invalidate')),
                config_digest TEXT NOT NULL REFERENCES config_revision(digest),
                payload_json TEXT NOT NULL,
                actor TEXT NOT NULL,
                at_ns INTEGER NOT NULL CHECK(at_ns >= 0)
            );
            """
        )

    def save_config_revision(self, payload: Mapping[str, Any], *, created_at_ns: int) -> str:
        document = canonical_json(payload)
        digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO config_revision(digest, payload_json, created_at_ns)
                VALUES (?, ?, ?)
                """,
                (digest, document, created_at_ns),
            )
        return digest

    def activate_config_revision(self, digest: str, *, activated_at_ns: int) -> None:
        with self._lock, self._connection:
            exists = self._connection.execute(
                "SELECT 1 FROM config_revision WHERE digest = ?", (digest,)
            ).fetchone()
            if exists is None:
                raise ValueError(f"config revision {digest!r} does not exist")
            self._connection.execute(
                """
                INSERT INTO active_config(singleton, digest, activated_at_ns)
                VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    digest = excluded.digest,
                    activated_at_ns = excluded.activated_at_ns
                """,
                (digest, activated_at_ns),
            )

    def config_revision(self, digest: str) -> Mapping[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM config_revision WHERE digest = ?", (digest,)
            ).fetchone()
        return None if row is None else json.loads(row["payload_json"])

    def active_config_digest(self) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT digest FROM active_config WHERE singleton = 1"
            ).fetchone()
        return None if row is None else str(row["digest"])

    def save_setup_step_result(self, result: SetupStepResult) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO setup_step_result(
                    step, status, config_digest, payload_json, actor, completed_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(step) DO UPDATE SET
                    status = excluded.status,
                    config_digest = excluded.config_digest,
                    payload_json = excluded.payload_json,
                    actor = excluded.actor,
                    completed_at_ns = excluded.completed_at_ns
                """,
                (
                    result.step.value,
                    result.status.value,
                    result.config_digest,
                    canonical_json(result.payload),
                    result.actor,
                    result.completed_at_ns,
                ),
            )

    def setup_step_results(self) -> list[SetupStepResult]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM setup_step_result").fetchall()
        by_step = {
            SetupStep(row["step"]): SetupStepResult(
                step=SetupStep(row["step"]),
                status=SetupStepStatus(row["status"]),
                config_digest=row["config_digest"],
                payload=json.loads(row["payload_json"]),
                actor=row["actor"],
                completed_at_ns=row["completed_at_ns"],
            )
            for row in rows
        }
        return [by_step[step] for step in SetupStep if step in by_step]

    def delete_setup_step_results(
        self,
        steps: set[SetupStep],
        *,
        config_digest: str,
        actor: str,
        reason: str,
        at_ns: int,
    ) -> None:
        with self._lock, self._connection:
            for step in sorted(steps, key=lambda item: list(SetupStep).index(item)):
                cursor = self._connection.execute(
                    "DELETE FROM setup_step_result WHERE step = ?", (step.value,)
                )
                if cursor.rowcount:
                    self._connection.execute(
                        """
                        INSERT INTO setup_transition(
                            step, action, config_digest, payload_json, actor, at_ns
                        ) VALUES (?, 'invalidate', ?, ?, ?, ?)
                        """,
                        (
                            step.value,
                            config_digest,
                            canonical_json({"reason": reason}),
                            actor,
                            at_ns,
                        ),
                    )

    def append_setup_transition(
        self,
        result: SetupStepResult,
        *,
        action: str,
    ) -> None:
        if action not in {"complete", "skip"}:
            raise ValueError("setup transition action is invalid")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO setup_transition(
                    step, action, config_digest, payload_json, actor, at_ns
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    result.step.value,
                    action,
                    result.config_digest,
                    canonical_json(result.payload),
                    result.actor,
                    result.completed_at_ns,
                ),
            )

    def append_event(
        self,
        *,
        session_id: str,
        at_ns: int,
        kind: str,
        payload: Mapping[str, Any],
    ) -> EventRecord:
        document = canonical_json(payload)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO event(session_id, at_ns, kind, payload_json) VALUES (?, ?, ?, ?)",
                (session_id, at_ns, kind, document),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return an event sequence")
            sequence = cursor.lastrowid
        return EventRecord(sequence, session_id, at_ns, kind, json.loads(document))

    def timeline(self, *, session_id: str, limit: int = 500) -> list[EventRecord]:
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be in [1, 10000]")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT sequence, session_id, at_ns, kind, payload_json
                FROM event
                WHERE session_id = ?
                ORDER BY at_ns, sequence
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [
            EventRecord(
                sequence=row["sequence"],
                session_id=row["session_id"],
                at_ns=row["at_ns"],
                kind=row["kind"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def save_feature_window(self, window: FeatureWindow) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO feature_window(
                    session_id, start_ns, end_ns, schema_version, values_json, missing_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    window.session_id,
                    window.start_ns,
                    window.end_ns,
                    window.schema_version,
                    canonical_json(window.values),
                    json.dumps(sorted(window.missing), separators=(",", ":")),
                ),
            )

    def feature_windows(self, *, session_id: str | None = None) -> list[FeatureWindow]:
        query = """
            SELECT session_id, start_ns, end_ns, schema_version, values_json, missing_json
            FROM feature_window
        """
        parameters: tuple[object, ...] = ()
        if session_id is not None:
            query += " WHERE session_id = ?"
            parameters = (session_id,)
        query += " ORDER BY session_id, start_ns, end_ns"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [
            FeatureWindow(
                schema_version=row["schema_version"],
                session_id=row["session_id"],
                start_ns=row["start_ns"],
                end_ns=row["end_ns"],
                values=feature_values(json.loads(row["values_json"])),
                missing=frozenset(json.loads(row["missing_json"])),
            )
            for row in rows
        ]

    def add_label(
        self,
        *,
        session_id: str,
        start_ns: int,
        end_ns: int,
        label: Classification,
        boundary: BoundaryKind = BoundaryKind.NONE,
        confidence: float = 1.0,
        author: str,
        created_at_ns: int,
        supersedes_id: str | None = None,
        notes: str = "",
    ) -> LabelRevision:
        revision = LabelRevision(
            revision_id=uuid4().hex,
            session_id=session_id,
            start_ns=start_ns,
            end_ns=end_ns,
            label=label,
            boundary=boundary,
            confidence=confidence,
            author=author,
            created_at_ns=created_at_ns,
            supersedes_id=supersedes_id,
            notes=notes,
        )
        self.save_label_revision(revision)
        return revision

    def save_label_revision(self, revision: LabelRevision) -> None:
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT * FROM label_revision WHERE revision_id = ?",
                (revision.revision_id,),
            ).fetchone()
            if existing is not None:
                if self._label_from_row(existing) != revision:
                    raise ValueError(f"label revision collision for {revision.revision_id!r}")
                return
            if revision.supersedes_id is not None:
                old = self._connection.execute(
                    "SELECT session_id FROM label_revision WHERE revision_id = ?",
                    (revision.supersedes_id,),
                ).fetchone()
                if old is None:
                    raise ValueError(f"superseded label {revision.supersedes_id!r} does not exist")
                if old["session_id"] != revision.session_id:
                    raise ValueError("a label cannot supersede a different session")
                already = self._connection.execute(
                    "SELECT 1 FROM label_revision WHERE supersedes_id = ?",
                    (revision.supersedes_id,),
                ).fetchone()
                if already is not None:
                    raise ValueError("label revision has already been superseded")
            self._connection.execute(
                """
                INSERT INTO label_revision(
                    revision_id, session_id, start_ns, end_ns, label, boundary,
                    confidence, author, created_at_ns, supersedes_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision.revision_id,
                    revision.session_id,
                    revision.start_ns,
                    revision.end_ns,
                    revision.label.value,
                    revision.boundary.value,
                    revision.confidence,
                    revision.author,
                    revision.created_at_ns,
                    revision.supersedes_id,
                    revision.notes,
                ),
            )
            self._connection.execute(
                """
                UPDATE artifact
                SET protected = 1
                WHERE digest IN (
                    SELECT digest FROM artifact_span
                    WHERE session_id = ? AND start_ns < ? AND end_ns > ?
                )
                """,
                (revision.session_id, revision.end_ns, revision.start_ns),
            )

    def labels(
        self,
        *,
        session_id: str | None = None,
        effective_only: bool = False,
    ) -> list[LabelRevision]:
        clauses: list[str] = []
        parameters: list[object] = []
        if session_id is not None:
            clauses.append("current.session_id = ?")
            parameters.append(session_id)
        if effective_only:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM label_revision newer "
                "WHERE newer.supersedes_id = current.revision_id)"
            )
        query = "SELECT current.* FROM label_revision current"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY current.session_id, current.start_ns, current.created_at_ns"
        with self._lock:
            rows = self._connection.execute(query, tuple(parameters)).fetchall()
        return [self._label_from_row(row) for row in rows]

    def label_revision(
        self,
        revision_id: str,
        *,
        effective_only: bool = False,
    ) -> LabelRevision | None:
        query = "SELECT current.* FROM label_revision current WHERE current.revision_id = ?"
        if effective_only:
            query += (
                " AND NOT EXISTS (SELECT 1 FROM label_revision newer "
                "WHERE newer.supersedes_id = current.revision_id)"
            )
        with self._lock:
            row = self._connection.execute(query, (revision_id,)).fetchone()
        return self._label_from_row(row) if row is not None else None

    @staticmethod
    def _label_from_row(row: sqlite3.Row) -> LabelRevision:
        return LabelRevision(
            revision_id=row["revision_id"],
            session_id=row["session_id"],
            start_ns=row["start_ns"],
            end_ns=row["end_ns"],
            label=Classification(row["label"]),
            boundary=BoundaryKind(row["boundary"]),
            confidence=row["confidence"],
            author=row["author"],
            created_at_ns=row["created_at_ns"],
            supersedes_id=row["supersedes_id"],
            notes=row["notes"],
        )

    def save_fingerprint_reference(
        self,
        reference: FingerprintReference,
        landmarks: tuple[AudioLandmark, ...],
    ) -> None:
        if len(landmarks) != reference.landmark_count:
            raise ValueError("fingerprint landmark count does not match reference")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO fingerprint_ad(
                    ad_id, name, label_revision_id, session_id, start_ns, end_ns,
                    duration_ms, fingerprint_version, landmark_count, status, created_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(reference.ad_id),
                    reference.name,
                    reference.label_revision_id,
                    reference.session_id,
                    reference.start_ns,
                    reference.end_ns,
                    reference.duration_ms,
                    reference.fingerprint_version,
                    reference.landmark_count,
                    reference.status.value,
                    reference.created_at_ns,
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO fingerprint_hash(ad_id, hash_value, anchor_ms)
                VALUES (?, ?, ?)
                """,
                (
                    (str(reference.ad_id), landmark.hash_value, landmark.anchor_ms)
                    for landmark in landmarks
                ),
            )

    def fingerprints(
        self,
        *,
        status: FingerprintStatus | None = None,
    ) -> list[FingerprintReference]:
        query = "SELECT * FROM fingerprint_ad"
        parameters: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status.value,)
        query += " ORDER BY created_at_ns, ad_id"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [self._fingerprint_from_row(row) for row in rows]

    def fingerprint(self, ad_id: UUID) -> FingerprintReference | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM fingerprint_ad WHERE ad_id = ?", (str(ad_id),)
            ).fetchone()
        return self._fingerprint_from_row(row) if row is not None else None

    @staticmethod
    def _fingerprint_from_row(row: sqlite3.Row) -> FingerprintReference:
        return FingerprintReference(
            ad_id=UUID(row["ad_id"]),
            name=row["name"],
            label_revision_id=row["label_revision_id"],
            session_id=row["session_id"],
            start_ns=row["start_ns"],
            end_ns=row["end_ns"],
            duration_ms=row["duration_ms"],
            fingerprint_version=row["fingerprint_version"],
            landmark_count=row["landmark_count"],
            status=FingerprintStatus(row["status"]),
            created_at_ns=row["created_at_ns"],
        )

    def set_fingerprint_status(self, ad_id: UUID, status: FingerprintStatus) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE fingerprint_ad SET status = ? WHERE ad_id = ?",
                (status.value, str(ad_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"fingerprint {ad_id} does not exist")

    def remove_fingerprint(self, ad_id: UUID) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM fingerprint_ad WHERE ad_id = ?", (str(ad_id),)
            )
            if cursor.rowcount != 1:
                raise ValueError(f"fingerprint {ad_id} does not exist")

    def fingerprint_hash_hits(
        self,
        hash_values: set[int],
        *,
        active_only: bool = True,
    ) -> list[FingerprintHashHit]:
        if not hash_values:
            return []
        values = sorted(hash_values)
        hits: list[FingerprintHashHit] = []
        with self._lock:
            for start in range(0, len(values), 500):
                batch = values[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    "SELECT item.hash_value, item.ad_id, item.anchor_ms "
                    "FROM fingerprint_hash item JOIN fingerprint_ad ad ON ad.ad_id = item.ad_id "
                    f"WHERE item.hash_value IN ({placeholders})"
                )
                parameters: list[object] = list(batch)
                if active_only:
                    query += " AND ad.status = ?"
                    parameters.append(FingerprintStatus.ACTIVE.value)
                rows = self._connection.execute(query, tuple(parameters)).fetchall()
                hits.extend(
                    FingerprintHashHit(
                        hash_value=row["hash_value"],
                        ad_id=UUID(row["ad_id"]),
                        anchor_ms=row["anchor_ms"],
                    )
                    for row in rows
                )
        return hits

    def save_artifact(self, artifact: ArtifactRecord) -> None:
        with self._lock, self._connection:
            label_overlap = False
            if artifact.session_id is not None:
                label_overlap = (
                    self._connection.execute(
                        """
                        SELECT 1 FROM label_revision
                        WHERE session_id = ? AND start_ns < ? AND end_ns > ?
                        LIMIT 1
                        """,
                        (artifact.session_id, artifact.end_ns, artifact.start_ns),
                    ).fetchone()
                    is not None
                )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO artifact(
                    digest, relative_path, size_bytes, media_type, protected, created_at_ns,
                    session_id, start_ns, end_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.digest,
                    artifact.relative_path,
                    artifact.size_bytes,
                    artifact.media_type,
                    int(artifact.protected or label_overlap),
                    artifact.created_at_ns,
                    artifact.session_id,
                    artifact.start_ns,
                    artifact.end_ns,
                ),
            )
            if artifact.session_id is not None:
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO artifact_span(digest, session_id, start_ns, end_ns)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        artifact.digest,
                        artifact.session_id,
                        artifact.start_ns,
                        artifact.end_ns,
                    ),
                )
            if label_overlap:
                self._connection.execute(
                    "UPDATE artifact SET protected = 1 WHERE digest = ?",
                    (artifact.digest,),
                )

    def artifacts(self) -> list[ArtifactRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM artifact ORDER BY created_at_ns, digest"
            ).fetchall()
        return [
            ArtifactRecord(
                digest=row["digest"],
                relative_path=row["relative_path"],
                size_bytes=row["size_bytes"],
                media_type=row["media_type"],
                protected=bool(row["protected"]),
                created_at_ns=row["created_at_ns"],
                session_id=row["session_id"],
                start_ns=row["start_ns"],
                end_ns=row["end_ns"],
            )
            for row in rows
        ]

    def delete_artifact(self, digest: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM artifact WHERE digest = ? AND protected = 0", (digest,)
            )

    def protect_artifact(self, digest: str) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE artifact SET protected = 1 WHERE digest = ?", (digest,)
            )
            if cursor.rowcount != 1:
                raise ValueError(f"artifact {digest!r} does not exist")

    def save_dataset_manifest(
        self,
        digest: str,
        payload: Mapping[str, Any],
        *,
        created_at_ns: int,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO dataset_manifest(digest, payload_json, created_at_ns)
                VALUES (?, ?, ?)
                """,
                (digest, canonical_json(payload), created_at_ns),
            )

    def lock_test_partition(
        self,
        dataset_digest: str,
        sessions: set[str] | frozenset[str],
        *,
        grouping_digest: str,
        created_at_ns: int,
    ) -> tuple[str, ...]:
        ordered = tuple(sorted(sessions))
        document = json.dumps(ordered, separators=(",", ":"))
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT sessions_json, grouping_digest FROM test_partition_lock "
                "WHERE dataset_digest = ?",
                (dataset_digest,),
            ).fetchone()
            if row is not None:
                existing = tuple(str(item) for item in json.loads(row["sessions_json"]))
                if existing != ordered or row["grouping_digest"] != grouping_digest:
                    raise ValueError("final test partition is already locked differently")
                return existing
            self._connection.execute(
                """
                INSERT INTO test_partition_lock(
                    dataset_digest, sessions_json, grouping_digest, created_at_ns
                ) VALUES (?, ?, ?, ?)
                """,
                (dataset_digest, document, grouping_digest, created_at_ns),
            )
        return ordered

    def save_evaluation_run(self, record: EvaluationRunRecord) -> None:
        document = canonical_json(record.payload)
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT * FROM evaluation_run WHERE digest = ?", (record.digest,)
            ).fetchone()
            if existing is not None:
                if self._evaluation_from_row(existing) != record:
                    raise ValueError(f"evaluation run collision for {record.digest!r}")
                return
            self._connection.execute(
                """
                INSERT INTO evaluation_run(
                    digest, dataset_digest, report_digest, report_path, status,
                    payload_json, created_at_ns, completed_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.digest,
                    record.dataset_digest,
                    record.report_digest,
                    record.report_path,
                    record.status,
                    document,
                    record.created_at_ns,
                    record.completed_at_ns,
                ),
            )

    def evaluation_run(self, digest: str) -> EvaluationRunRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM evaluation_run WHERE digest = ?", (digest,)
            ).fetchone()
        return self._evaluation_from_row(row) if row is not None else None

    def evaluation_runs(self) -> list[EvaluationRunRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM evaluation_run ORDER BY created_at_ns, digest"
            ).fetchall()
        return [self._evaluation_from_row(row) for row in rows]

    @staticmethod
    def _evaluation_from_row(row: sqlite3.Row) -> EvaluationRunRecord:
        return EvaluationRunRecord(
            digest=row["digest"],
            dataset_digest=row["dataset_digest"],
            report_digest=row["report_digest"],
            report_path=row["report_path"],
            status=row["status"],
            payload=json.loads(row["payload_json"]),
            created_at_ns=row["created_at_ns"],
            completed_at_ns=row["completed_at_ns"],
        )

    def register_model(self, record: RegisteredModelRecord) -> None:
        with self._lock, self._connection:
            if self.evaluation_run(record.evaluation_digest) is None:
                raise ValueError("model registration requires an existing evaluation run")
            existing = self._connection.execute(
                "SELECT item.*, active.model_id AS active_id "
                "FROM registered_model item LEFT JOIN active_model active "
                "ON active.model_id = item.model_id WHERE item.model_id = ?",
                (record.model_id,),
            ).fetchone()
            if existing is not None:
                existing_record = self._registered_model_from_row(existing)
                if existing_record != replace(record, active=existing_record.active):
                    raise ValueError(f"registered model collision for {record.model_id!r}")
                return
            self._connection.execute(
                """
                INSERT INTO registered_model(
                    model_id, bundle_path, dataset_digest, evaluation_digest,
                    feature_schema, aggregation_schema, modality, algorithm,
                    registered_by, registered_at_ns, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.model_id,
                    record.bundle_path,
                    record.dataset_digest,
                    record.evaluation_digest,
                    record.feature_schema,
                    record.aggregation_schema,
                    record.modality,
                    record.algorithm,
                    record.registered_by,
                    record.registered_at_ns,
                    record.notes,
                ),
            )

    def registered_model(self, model_id: str) -> RegisteredModelRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT item.*, active.model_id AS active_id "
                "FROM registered_model item LEFT JOIN active_model active "
                "ON active.model_id = item.model_id WHERE item.model_id = ?",
                (model_id,),
            ).fetchone()
        return self._registered_model_from_row(row) if row is not None else None

    def registered_models(self) -> list[RegisteredModelRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT item.*, active.model_id AS active_id "
                "FROM registered_model item LEFT JOIN active_model active "
                "ON active.model_id = item.model_id "
                "ORDER BY item.registered_at_ns, item.model_id"
            ).fetchall()
        return [self._registered_model_from_row(row) for row in rows]

    def active_model(self) -> RegisteredModelRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT item.*, active.model_id AS active_id "
                "FROM active_model active JOIN registered_model item "
                "ON item.model_id = active.model_id WHERE active.singleton = 1"
            ).fetchone()
        return self._registered_model_from_row(row) if row is not None else None

    @staticmethod
    def _registered_model_from_row(row: sqlite3.Row) -> RegisteredModelRecord:
        return RegisteredModelRecord(
            model_id=row["model_id"],
            bundle_path=row["bundle_path"],
            dataset_digest=row["dataset_digest"],
            evaluation_digest=row["evaluation_digest"],
            feature_schema=row["feature_schema"],
            aggregation_schema=row["aggregation_schema"],
            modality=row["modality"],
            algorithm=row["algorithm"],
            registered_by=row["registered_by"],
            registered_at_ns=row["registered_at_ns"],
            notes=row["notes"],
            active=row["active_id"] is not None,
        )

    def activate_registered_model(
        self,
        model_id: str,
        *,
        actor: str,
        reason: str,
        config_digest: str,
        at_ns: int,
        action: str = "activate",
    ) -> ModelActivationRecord:
        if action not in {"activate", "rollback"}:
            raise ValueError("model activation action is invalid")
        with self._lock, self._connection:
            requested = self._connection.execute(
                "SELECT 1 FROM registered_model WHERE model_id = ?", (model_id,)
            ).fetchone()
            if requested is None:
                raise ValueError(f"registered model {model_id!r} does not exist")
            active = self._connection.execute(
                "SELECT model_id FROM active_model WHERE singleton = 1"
            ).fetchone()
            previous = None if active is None else str(active["model_id"])
            cursor = self._connection.execute(
                """
                INSERT INTO model_activation(
                    action, requested_model_id, previous_model_id, actor, reason,
                    config_digest, at_ns, outcome, explanation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'succeeded', 'model pointer updated')
                """,
                (action, model_id, previous, actor, reason, config_digest, at_ns),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return an activation sequence")
            sequence = cursor.lastrowid
            self._connection.execute(
                """
                INSERT INTO active_model(singleton, model_id, activation_sequence)
                VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    model_id = excluded.model_id,
                    activation_sequence = excluded.activation_sequence
                """,
                (model_id, sequence),
            )
        return ModelActivationRecord(
            sequence=sequence,
            action=action,
            requested_model_id=model_id,
            previous_model_id=previous,
            actor=actor,
            reason=reason,
            config_digest=config_digest,
            at_ns=at_ns,
            outcome="succeeded",
            explanation="model pointer updated",
        )

    def record_model_activation_failure(
        self,
        model_id: str,
        *,
        actor: str,
        reason: str,
        config_digest: str,
        at_ns: int,
        action: str,
        explanation: str,
    ) -> ModelActivationRecord:
        if action not in {"activate", "rollback"}:
            raise ValueError("model activation action is invalid")
        with self._lock, self._connection:
            active = self._connection.execute(
                "SELECT model_id FROM active_model WHERE singleton = 1"
            ).fetchone()
            previous = None if active is None else str(active["model_id"])
            cursor = self._connection.execute(
                """
                INSERT INTO model_activation(
                    action, requested_model_id, previous_model_id, actor, reason,
                    config_digest, at_ns, outcome, explanation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'failed', ?)
                """,
                (
                    action,
                    model_id,
                    previous,
                    actor,
                    reason,
                    config_digest,
                    at_ns,
                    explanation,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return an activation sequence")
            sequence = cursor.lastrowid
        return ModelActivationRecord(
            sequence=sequence,
            action=action,
            requested_model_id=model_id,
            previous_model_id=previous,
            actor=actor,
            reason=reason,
            config_digest=config_digest,
            at_ns=at_ns,
            outcome="failed",
            explanation=explanation,
        )

    def model_activation(self, sequence: int) -> ModelActivationRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM model_activation WHERE sequence = ?", (sequence,)
            ).fetchone()
        return self._activation_from_row(row) if row is not None else None

    def model_activations(self) -> list[ModelActivationRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM model_activation ORDER BY sequence"
            ).fetchall()
        return [self._activation_from_row(row) for row in rows]

    @staticmethod
    def _activation_from_row(row: sqlite3.Row) -> ModelActivationRecord:
        return ModelActivationRecord(
            sequence=row["sequence"],
            action=row["action"],
            requested_model_id=row["requested_model_id"],
            previous_model_id=row["previous_model_id"],
            actor=row["actor"],
            reason=row["reason"],
            config_digest=row["config_digest"],
            at_ns=row["at_ns"],
            outcome=row["outcome"],
            explanation=row["explanation"],
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
