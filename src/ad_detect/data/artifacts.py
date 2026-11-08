"""Content-addressed local artifacts with protected-data retention rules."""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .database import ArtifactRecord, SQLiteStore


@dataclass(frozen=True, slots=True)
class RetentionReport:
    quota_bytes: int
    bytes_before: int
    bytes_after: int
    deleted_digests: tuple[str, ...]
    protected_bytes: int

    @property
    def quota_satisfied(self) -> bool:
        return self.bytes_after <= self.quota_bytes


class LocalArtifactStore:
    def __init__(self, root: str | Path, repository: SQLiteStore, *, quota_bytes: int) -> None:
        if quota_bytes < 1:
            raise ValueError("artifact quota must be positive")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = repository
        self.quota_bytes = quota_bytes

    def retain(
        self,
        data: bytes,
        *,
        media_type: str,
        created_at_ns: int,
        protected: bool = False,
        session_id: str | None = None,
        start_ns: int | None = None,
        end_ns: int | None = None,
    ) -> ArtifactRecord:
        digest = hashlib.sha256(data).hexdigest()
        relative = Path(digest[:2]) / digest
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        artifact = ArtifactRecord(
            digest=digest,
            relative_path=str(relative),
            size_bytes=len(data),
            media_type=media_type,
            protected=protected,
            created_at_ns=created_at_ns,
            session_id=session_id,
            start_ns=start_ns,
            end_ns=end_ns,
        )
        self.repository.save_artifact(artifact)
        if protected:
            self.repository.protect_artifact(digest)
        return next(item for item in self.repository.artifacts() if item.digest == digest)

    def read(self, digest: str) -> bytes:
        artifact = next(
            (item for item in self.repository.artifacts() if item.digest == digest),
            None,
        )
        if artifact is None:
            raise KeyError(digest)
        return self._safe_path(artifact.relative_path).read_bytes()

    def _safe_path(self, relative_path: str) -> Path:
        root = self.root.resolve()
        path = (self.root / relative_path).resolve()
        if path != root and root not in path.parents:
            raise ValueError("artifact path escapes configured root")
        return path

    def enforce_quota(self) -> RetentionReport:
        artifacts = self.repository.artifacts()
        before = sum(item.size_bytes for item in artifacts)
        protected_bytes = sum(item.size_bytes for item in artifacts if item.protected)
        after = before
        deleted: list[str] = []
        for artifact in artifacts:
            if after <= self.quota_bytes:
                break
            if artifact.protected:
                continue
            path = self._safe_path(artifact.relative_path)
            with suppress(FileNotFoundError):
                path.unlink()
            self.repository.delete_artifact(artifact.digest)
            after -= artifact.size_bytes
            deleted.append(artifact.digest)
        return RetentionReport(
            quota_bytes=self.quota_bytes,
            bytes_before=before,
            bytes_after=after,
            deleted_digests=tuple(deleted),
            protected_bytes=protected_bytes,
        )
