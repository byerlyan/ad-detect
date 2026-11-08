"""Small SQLite repository with explicit schema migrations and canonical JSON."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from ad_detect.domain import (
    BoundaryKind,
    Classification,
    FeatureWindow,
    LabelRevision,
    feature_values,
)

SCHEMA_VERSION = 3


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
                self._connection.execute("UPDATE schema_version SET version = 3")

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

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
