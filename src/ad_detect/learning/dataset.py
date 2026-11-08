"""Immutable, hash-verified dataset manifests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ad_detect.data.database import SQLiteStore, canonical_json
from ad_detect.domain import (
    BoundaryKind,
    Classification,
    FeatureWindow,
    LabelRevision,
    feature_values,
)


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    digest: str
    path: Path
    payload: dict[str, Any]


def export_dataset(
    store: SQLiteStore,
    output_directory: str | Path,
    *,
    created_at_ns: int,
    session_ids: set[str] | None = None,
    provenance: Mapping[str, str] | None = None,
) -> DatasetManifest:
    features = store.feature_windows()
    labels = store.labels()
    if session_ids is not None:
        features = [item for item in features if item.session_id in session_ids]
        labels = [item for item in labels if item.session_id in session_ids]
    sessions = sorted({item.session_id for item in features} | {item.session_id for item in labels})
    payload: dict[str, Any] = {
        "format_version": 1,
        "created_at_ns": created_at_ns,
        "provenance": dict(sorted((provenance or {}).items())),
        "sessions": sessions,
        "feature_windows": [
            {
                "schema_version": item.schema_version,
                "session_id": item.session_id,
                "start_ns": item.start_ns,
                "end_ns": item.end_ns,
                "values": dict(sorted(item.values.items())),
                "missing": sorted(item.missing),
            }
            for item in features
        ],
        "labels": [
            {
                **asdict(item),
                "label": item.label.value,
                "boundary": item.boundary.value,
            }
            for item in labels
        ],
    }
    document = canonical_json(payload)
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.json"
    if not path.exists():
        with tempfile.NamedTemporaryFile(
            "w", dir=directory, delete=False, encoding="utf-8"
        ) as stream:
            temporary = Path(stream.name)
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    store.save_dataset_manifest(digest, payload, created_at_ns=created_at_ns)
    return DatasetManifest(digest=digest, path=path, payload=payload)


def load_dataset_manifest(path: str | Path) -> DatasetManifest:
    manifest_path = Path(path)
    raw = manifest_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("dataset manifest must contain a JSON object")
    document = canonical_json(payload)
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    if manifest_path.stem != digest:
        raise ValueError("dataset manifest filename does not match its content hash")
    return DatasetManifest(digest=digest, path=manifest_path, payload=payload)


def import_dataset(store: SQLiteStore, path: str | Path) -> DatasetManifest:
    """Verify and import an immutable dataset without rewriting label history."""
    manifest = load_dataset_manifest(path)
    payload = manifest.payload
    if payload.get("format_version") != 1:
        raise ValueError("unsupported dataset format version")
    raw_features = payload.get("feature_windows")
    raw_labels = payload.get("labels")
    if not isinstance(raw_features, list) or not isinstance(raw_labels, list):
        raise ValueError("dataset features and labels must be arrays")

    features: list[FeatureWindow] = []
    for raw in raw_features:
        if not isinstance(raw, dict) or not isinstance(raw.get("values"), dict):
            raise ValueError("invalid feature window in dataset")
        missing = raw.get("missing", [])
        if not isinstance(missing, list):
            raise ValueError("feature missingness must be an array")
        features.append(
            FeatureWindow(
                schema_version=str(raw["schema_version"]),
                session_id=str(raw["session_id"]),
                start_ns=int(raw["start_ns"]),
                end_ns=int(raw["end_ns"]),
                values=feature_values(raw["values"]),
                missing=frozenset(str(item) for item in missing),
            )
        )

    labels: list[LabelRevision] = []
    for raw in raw_labels:
        if not isinstance(raw, dict):
            raise ValueError("invalid label revision in dataset")
        supersedes = raw.get("supersedes_id")
        labels.append(
            LabelRevision(
                revision_id=str(raw["revision_id"]),
                session_id=str(raw["session_id"]),
                start_ns=int(raw["start_ns"]),
                end_ns=int(raw["end_ns"]),
                label=Classification(str(raw["label"])),
                boundary=BoundaryKind(str(raw["boundary"])),
                confidence=float(raw["confidence"]),
                author=str(raw["author"]),
                created_at_ns=int(raw["created_at_ns"]),
                supersedes_id=None if supersedes is None else str(supersedes),
                notes=str(raw.get("notes", "")),
            )
        )

    ordered_labels = _order_label_revisions(labels)
    for feature in features:
        store.save_feature_window(feature)
    for label in ordered_labels:
        store.save_label_revision(label)
    return manifest


def _order_label_revisions(labels: Sequence[LabelRevision]) -> list[LabelRevision]:
    """Put correction parents before children and reject incomplete histories."""
    by_id = {label.revision_id: label for label in labels}
    if len(by_id) != len(labels):
        raise ValueError("dataset contains duplicate label revision IDs")
    ordered: list[LabelRevision] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(label: LabelRevision) -> None:
        if label.revision_id in visited:
            return
        if label.revision_id in visiting:
            raise ValueError("dataset label correction history contains a cycle")
        visiting.add(label.revision_id)
        if label.supersedes_id is not None:
            parent = by_id.get(label.supersedes_id)
            if parent is None:
                raise ValueError("dataset label correction parent is missing")
            visit(parent)
        visiting.remove(label.revision_id)
        visited.add(label.revision_id)
        ordered.append(label)

    for label in labels:
        visit(label)
    return ordered
