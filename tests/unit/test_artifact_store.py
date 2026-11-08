from pathlib import Path

from ad_detect.data import LocalArtifactStore, SQLiteStore
from ad_detect.domain import Classification


def test_retention_deletes_oldest_unprotected_artifacts_only(tmp_path: Path) -> None:
    with SQLiteStore(":memory:") as repository:
        artifacts = LocalArtifactStore(tmp_path / "artifacts", repository, quota_bytes=6)
        protected = artifacts.retain(
            b"protected", media_type="application/octet-stream", created_at_ns=1, protected=True
        )
        old = artifacts.retain(b"old", media_type="application/octet-stream", created_at_ns=2)
        new = artifacts.retain(b"new", media_type="application/octet-stream", created_at_ns=3)

        report = artifacts.enforce_quota()

        assert not report.quota_satisfied
        assert set(report.deleted_digests) == {old.digest, new.digest}
        assert artifacts.read(protected.digest) == b"protected"
        assert [item.digest for item in repository.artifacts()] == [protected.digest]


def test_retain_is_content_addressed_and_can_promote_to_protected(tmp_path: Path) -> None:
    with SQLiteStore(":memory:") as repository:
        artifacts = LocalArtifactStore(tmp_path, repository, quota_bytes=100)
        first = artifacts.retain(b"same", media_type="video/mp4", created_at_ns=1)
        second = artifacts.retain(b"same", media_type="video/mp4", created_at_ns=2, protected=True)

        assert first.digest == second.digest
        assert second.protected
        assert len(repository.artifacts()) == 1


def test_label_automatically_protects_overlapping_artifact(tmp_path: Path) -> None:
    with SQLiteStore(":memory:") as repository:
        artifacts = LocalArtifactStore(tmp_path, repository, quota_bytes=1)
        clip = artifacts.retain(
            b"labeled clip",
            media_type="video/mp4",
            created_at_ns=1,
            session_id="session",
            start_ns=10,
            end_ns=20,
        )
        repository.add_label(
            session_id="session",
            start_ns=15,
            end_ns=25,
            label=Classification.COMMERCIAL,
            author="reviewer",
            created_at_ns=2,
        )

        report = artifacts.enforce_quota()

        assert not report.quota_satisfied
        assert report.deleted_digests == ()
        assert repository.artifacts()[0].protected
        assert artifacts.read(clip.digest) == b"labeled clip"
