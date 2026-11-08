"""Immutable Phase 7 evaluation runs and local JSON/HTML reports."""

from __future__ import annotations

import hashlib
import html
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ad_detect.config import Settings
from ad_detect.data import EvaluationRunRecord, SQLiteStore
from ad_detect.data.database import canonical_json
from ad_detect.detectors import SklearnModelDetector

from .aggregation import aggregate_feature_windows
from .dataset import import_dataset, load_dataset_manifest
from .examples import grouped_session_split, label_feature_windows
from .ml_replay import MLReplayEvaluator
from .model import evaluate_candidate, load_model_bundle
from .temporal_replay import TemporalComparisonEvaluator


@dataclass(frozen=True, slots=True)
class EvaluationArtifacts:
    record: EvaluationRunRecord
    directory: Path
    manifest_path: Path
    report_path: Path
    html_path: Path


async def run_evaluation(
    store: SQLiteStore,
    settings: Settings,
    dataset_path: str | Path,
    candidate_paths: list[Path],
    output_directory: str | Path,
    *,
    split_seed: str,
    source_revision: str,
    source_dirty: bool,
    created_at_ns: int | None = None,
) -> EvaluationArtifacts:
    created = created_at_ns if created_at_ns is not None else time.time_ns()
    dataset = load_dataset_manifest(dataset_path)
    with SQLiteStore(":memory:") as imported:
        import_dataset(imported, dataset_path)
        windows = imported.feature_windows()
        labels = imported.labels(effective_only=True)
    aggregated = aggregate_feature_windows(
        windows,
        schema_version=settings.ml.aggregation_schema,
    )
    examples = label_feature_windows(aggregated, labels)
    split = grouped_session_split(
        [example.features.session_id for example in examples],
        seed=split_seed,
    )
    session_ids = sorted(
        {window.session_id for window in windows} | {label.session_id for label in labels}
    )
    grouping_payload = {
        "format_version": 1,
        "groups": {session_id: session_id for session_id in session_ids},
        "split_seed": split_seed,
    }
    grouping_digest = hashlib.sha256(canonical_json(grouping_payload).encode()).hexdigest()
    store.lock_test_partition(
        dataset.digest,
        split.test,
        grouping_digest=grouping_digest,
        created_at_ns=created,
    )
    config_digest = store.save_config_revision(
        settings.model_dump(mode="json"),
        created_at_ns=created,
    )
    test_windows = [window for window in windows if window.session_id in split.test]
    test_aggregated = [window for window in aggregated if window.session_id in split.test]
    test_examples = [item for item in examples if item.features.session_id in split.test]
    test_labels = [label for label in labels if label.session_id in split.test]

    temporal = await TemporalComparisonEvaluator(
        settings.rules,
        settings.detection,
        settings.temporal,
        feature_schema=settings.features.schema_version,
        stale_after_ms=settings.capture.stale_after_ms,
        max_av_skew_ms=settings.capture.max_av_skew_ms,
    ).evaluate(test_windows, test_labels)
    candidate_reports: list[dict[str, Any]] = []
    candidate_digests: list[str] = []
    for candidate_path in candidate_paths:
        model, manifest = load_model_bundle(
            candidate_path,
            expected_feature_schema=settings.features.schema_version,
        )
        if manifest.dataset_digest != dataset.digest:
            raise ValueError("candidate model was trained from a different dataset")
        if (
            model.aggregation_schema
            != f"{settings.ml.aggregation_schema}:{settings.features.schema_version}"
        ):
            raise ValueError("candidate aggregation schema is incompatible")
        metrics = evaluate_candidate(model, test_examples)
        detector = SklearnModelDetector(
            model,
            model_digest=manifest.digest,
            inference_timeout_ms=settings.ml.inference_timeout_ms,
        )
        replay = await MLReplayEvaluator(detector).evaluate(test_aggregated, test_labels)
        per_session: dict[str, Any] = {}
        for session_id in sorted(split.test):
            session_windows = [
                window for window in test_aggregated if window.session_id == session_id
            ]
            session_labels = [label for label in test_labels if label.session_id == session_id]
            per_session[session_id] = asdict(
                (
                    await MLReplayEvaluator(detector).evaluate(session_windows, session_labels)
                ).metrics
            )
        candidate_digests.append(manifest.digest)
        candidate_reports.append(
            {
                "bundle_digest": manifest.digest,
                "modality": manifest.modality.value,
                "algorithm": manifest.algorithm.value,
                "calibration_method": manifest.calibration_method,
                "classification_metrics": asdict(metrics),
                "causal_replay_metrics": asdict(replay.metrics),
                "session_metrics": per_session,
            }
        )

    completed = max(created, time.time_ns())
    report_payload: dict[str, Any] = {
        "format_version": 1,
        "dataset_digest": dataset.digest,
        "config_digest": config_digest,
        "source": {"revision": source_revision, "dirty": source_dirty},
        "split": {
            "seed": split_seed,
            "grouping_digest": grouping_digest,
            "train": sorted(split.train),
            "calibration": sorted(split.calibration),
            "test": sorted(split.test),
            "final_test_locked": True,
        },
        "temporal_rules_matrix": {
            strategy.value: asdict(result.metrics) for strategy, result in temporal.results.items()
        },
        "ml_candidates": candidate_reports,
        "unavailable_baselines": {
            "fingerprint": "dataset manifest contains no PCM query audio",
            "combined_detector_fusion": "dataset does not yet persist aligned detector evidence",
        },
        "host": _host_profile(),
        "created_at_ns": created,
        "completed_at_ns": completed,
        "accuracy_claim": None,
    }
    report_document = canonical_json(report_payload)
    report_digest = hashlib.sha256(report_document.encode()).hexdigest()
    manifest_payload: dict[str, Any] = {
        "format_version": 1,
        "dataset_digest": dataset.digest,
        "config_digest": config_digest,
        "report_digest": report_digest,
        "model_digests": sorted(candidate_digests),
        "source_revision": source_revision,
        "source_dirty": source_dirty,
        "split": report_payload["split"],
        "schemas": {
            "feature": settings.features.schema_version,
            "aggregation": settings.ml.aggregation_schema,
            "fingerprint": settings.fingerprint.version,
            "rules": settings.rules.version,
            "temporal": settings.temporal.strategy.value,
            "report": "evaluation-report-v1",
        },
        "command": {
            "name": "evaluation-run",
            "split_seed": split_seed,
            "candidate_count": len(candidate_paths),
        },
        "host": report_payload["host"],
        "created_at_ns": created,
        "completed_at_ns": completed,
        "status": "complete",
    }
    manifest_document = canonical_json(manifest_payload)
    digest = hashlib.sha256(manifest_document.encode()).hexdigest()
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="evaluation-", dir=output))
    target = output / digest
    try:
        (temporary / "manifest.json").write_text(manifest_document, encoding="utf-8")
        (temporary / "report.json").write_text(report_document, encoding="utf-8")
        (temporary / "report.html").write_text(
            _html_report(digest, report_payload),
            encoding="utf-8",
        )
        if target.exists():
            shutil.rmtree(temporary)
        else:
            os.replace(temporary, target)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    record = EvaluationRunRecord(
        digest=digest,
        dataset_digest=dataset.digest,
        report_digest=report_digest,
        report_path=str(target / "report.json"),
        status="complete",
        payload=manifest_payload,
        created_at_ns=created,
        completed_at_ns=completed,
    )
    store.save_evaluation_run(record)
    return EvaluationArtifacts(
        record=record,
        directory=target,
        manifest_path=target / "manifest.json",
        report_path=target / "report.json",
        html_path=target / "report.html",
    )


def _host_profile() -> dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "implementation": sys.implementation.name,
    }


def _html_report(run_digest: str, payload: dict[str, Any]) -> str:
    document = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Ad Detect evaluation {html.escape(run_digest)}</title>"
        "<style>body{font:16px system-ui;max-width:80rem;margin:auto;padding:1rem;}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#111;color:#eee;padding:1rem;}"
        ".warning{border-left:.4rem solid #b60;padding:.75rem;background:#fff4d6;}</style>"
        "</head><body><h1>Ad Detect evaluation report</h1>"
        '<p class="warning">No accuracy claim is made until representative held-out '
        "recordings and hardware acceptance are complete.</p>"
        f"<p>Run digest: <code>{html.escape(run_digest)}</code></p>"
        f"<pre>{html.escape(document)}</pre></body></html>"
    )
