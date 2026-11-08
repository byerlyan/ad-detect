"""Command-line entry points for the local server and deterministic demo."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
import wave
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from ad_detect.api import create_app
from ad_detect.app import DetectionRuntime, LiveShadowPipeline
from ad_detect.capture import CaptureSupervisor, SimulatedCapture
from ad_detect.capture.ffmpeg import FFmpegAVFoundation
from ad_detect.config import load_settings
from ad_detect.data import SQLiteStore
from ad_detect.domain import (
    AudioChunk,
    BoundaryKind,
    Classification,
    FingerprintReference,
    FingerprintStatus,
    MediaStamp,
    OperatingMode,
    TrustedAdSample,
)
from ad_detect.fingerprint import SQLiteFingerprintIndex
from ad_detect.learning import (
    REGISTER_MODEL,
    AuthorizedActor,
    MLReplayEvaluator,
    ModelAlgorithm,
    ModelModality,
    ModelRegistry,
    RulesReplayEvaluator,
    TemporalComparisonEvaluator,
    aggregate_feature_windows,
    evaluate_candidate,
    export_dataset,
    grouped_session_split,
    import_dataset,
    label_feature_windows,
    load_dataset_manifest,
    run_evaluation,
    save_model_bundle,
    train_candidate,
)
from ad_detect.observability import configure_logging


async def _simulate(config_path: Path, scores: str) -> int:
    settings = load_settings(config_path)
    parsed = [
        None if value.strip().lower() == "unknown" else float(value) for value in scores.split(",")
    ]
    with SQLiteStore(":memory:") as store:
        runtime = DetectionRuntime(settings, store, session_id="cli-simulation")
        await runtime.start()
        for tick in SimulatedCapture(parsed):
            decision = await runtime.process_tick(tick)
            print(
                json.dumps(
                    {
                        "at_ns": decision.at_ns,
                        "classification": decision.classification.value,
                        "break_active": decision.break_active,
                        "action": decision.action.value,
                    },
                    sort_keys=True,
                )
            )
        await runtime.stop()
    return 0


async def _capture_devices(config_path: Path) -> int:
    settings = load_settings(config_path)
    adapter = FFmpegAVFoundation(settings.capture)
    version = await adapter.version()
    devices = await adapter.discover()
    print(
        json.dumps(
            {
                "ffmpeg": version,
                "devices": [
                    {"kind": item.kind.value, "index": item.index, "name": item.name}
                    for item in devices
                ],
            },
            sort_keys=True,
        )
    )
    return 0


async def _capture_probe(config_path: Path) -> int:
    settings = load_settings(config_path)
    result = await FFmpegAVFoundation(settings.capture).probe()
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.success else 2


async def _appletv_devices(config_path: Path) -> int:
    from ad_detect.intervention.appletv import AppleTVIntervention

    settings = load_settings(config_path)
    adapter = AppleTVIntervention(settings.intervention)
    devices = await adapter.discover()
    print(json.dumps([asdict(device) for device in devices], sort_keys=True))
    return 0


def _add_label(
    config_path: Path,
    *,
    session_id: str,
    start_ns: int,
    end_ns: int,
    label: str,
    boundary: str,
    confidence: float,
    author: str,
    supersedes_id: str | None,
    notes: str,
) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        revision = store.add_label(
            session_id=session_id,
            start_ns=start_ns,
            end_ns=end_ns,
            label=Classification(label),
            boundary=BoundaryKind(boundary),
            confidence=confidence,
            author=author,
            created_at_ns=time.time_ns(),
            supersedes_id=supersedes_id,
            notes=notes,
        )
    print(json.dumps(asdict(revision), sort_keys=True))
    return 0


def _export_dataset(config_path: Path, output: Path, sessions: list[str]) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        config_digest = store.save_config_revision(
            settings.model_dump(mode="json"), created_at_ns=time.time_ns()
        )
        manifest = export_dataset(
            store,
            output,
            created_at_ns=time.time_ns(),
            session_ids=set(sessions) if sessions else None,
            provenance={
                "config_digest": config_digest,
                "feature_schema": settings.features.schema_version,
                "rules_version": settings.rules.version,
            },
        )
    print(json.dumps({"digest": manifest.digest, "path": str(manifest.path)}, sort_keys=True))
    return 0


def _import_dataset(config_path: Path, manifest_path: Path) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        manifest = import_dataset(store, manifest_path)
    print(json.dumps({"digest": manifest.digest, "path": str(manifest.path)}, sort_keys=True))
    return 0


async def _rules_replay(config_path: Path, session_id: str | None) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        evaluator = RulesReplayEvaluator(
            settings.rules,
            settings.detection,
            feature_schema=settings.features.schema_version,
            stale_after_ms=settings.capture.stale_after_ms,
            max_av_skew_ms=settings.capture.max_av_skew_ms,
            temporal=settings.temporal,
        )
        result = await evaluator.evaluate(
            store.feature_windows(session_id=session_id),
            store.labels(session_id=session_id, effective_only=True),
        )
    print(json.dumps(asdict(result.metrics), sort_keys=True))
    return 0


async def _temporal_replay(config_path: Path, session_id: str | None) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        result = await TemporalComparisonEvaluator(
            settings.rules,
            settings.detection,
            settings.temporal,
            feature_schema=settings.features.schema_version,
            stale_after_ms=settings.capture.stale_after_ms,
            max_av_skew_ms=settings.capture.max_av_skew_ms,
        ).evaluate(
            store.feature_windows(session_id=session_id),
            store.labels(session_id=session_id, effective_only=True),
        )
    print(
        json.dumps(
            {strategy.value: asdict(replay.metrics) for strategy, replay in result.results.items()},
            sort_keys=True,
        )
    )
    return 0


def _source_state() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return revision, dirty
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True


async def _evaluation_run(
    config_path: Path,
    dataset_path: Path,
    candidate_paths: list[Path],
    output_directory: Path,
    *,
    split_seed: str,
) -> int:
    settings = load_settings(config_path)
    revision, dirty = _source_state()
    with SQLiteStore(settings.storage.database_path) as store:
        result = await run_evaluation(
            store,
            settings,
            dataset_path,
            candidate_paths,
            output_directory,
            split_seed=split_seed,
            source_revision=revision,
            source_dirty=dirty,
        )
    print(
        json.dumps(
            {
                "digest": result.record.digest,
                "manifest": str(result.manifest_path),
                "report": str(result.report_path),
                "html": str(result.html_path),
                "accuracy_claim": None,
            },
            sort_keys=True,
        )
    )
    return 0


def _model_register(
    config_path: Path,
    bundle_path: Path,
    evaluation_digest: str,
    *,
    actor: str,
    notes: str,
) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        record = ModelRegistry(
            store,
            feature_schema=settings.features.schema_version,
            aggregation_schema=(
                f"{settings.ml.aggregation_schema}:{settings.features.schema_version}"
            ),
        ).register(
            bundle_path,
            evaluation_digest,
            authorization=AuthorizedActor(actor, frozenset({REGISTER_MODEL})),
            registered_at_ns=time.time_ns(),
            notes=notes,
        )
    print(json.dumps(asdict(record), sort_keys=True))
    return 0


def _model_list(config_path: Path) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        records = ModelRegistry(
            store,
            feature_schema=settings.features.schema_version,
            aggregation_schema=(
                f"{settings.ml.aggregation_schema}:{settings.features.schema_version}"
            ),
        ).models()
    print(json.dumps([asdict(record) for record in records], sort_keys=True))
    return 0


async def _capture_shadow(config_path: Path, session_id: str | None) -> int:
    settings = load_settings(config_path)
    resolved_session = session_id or f"capture-{uuid4().hex}"
    adapter = FFmpegAVFoundation(settings.capture)
    video, audio = await adapter.selected_devices()
    source = CaptureSupervisor(
        adapter,
        session_id=resolved_session,
        queue_capacity=settings.capture.queue_capacity,
    )
    with SQLiteStore(settings.storage.database_path) as store:
        config_digest = store.save_config_revision(
            settings.model_dump(mode="json"), created_at_ns=time.time_ns()
        )
        store.append_event(
            session_id=resolved_session,
            at_ns=time.monotonic_ns(),
            kind="session.started",
            payload={
                "config_digest": config_digest,
                "capture_source": "ffmpeg-avfoundation",
                "video_device": video.key,
                "audio_device": audio.key,
                "feature_schema": settings.features.schema_version,
                "rules_version": settings.rules.version,
                "operating_mode": OperatingMode.SHADOW.value,
            },
        )
        pipeline = LiveShadowPipeline(
            settings,
            store,
            session_id=resolved_session,
            source=source,
        )
        try:
            await pipeline.start()
            await pipeline.wait()
        finally:
            await pipeline.stop()
        snapshot = pipeline.snapshot()
        store.append_event(
            session_id=resolved_session,
            at_ns=time.monotonic_ns(),
            kind="session.stopped",
            payload={
                "records_processed": snapshot.records_processed,
                "windows_processed": snapshot.windows_processed,
                "queue_drops": snapshot.queue_drops,
                "capture_restarts": snapshot.capture_restarts,
                "capture_error": snapshot.capture_error or "",
                "processing_error": snapshot.processing_error or "",
            },
        )
    print(
        json.dumps(
            {
                "session_id": resolved_session,
                "records_processed": snapshot.records_processed,
                "windows_processed": snapshot.windows_processed,
                "queue_drops": snapshot.queue_drops,
                "capture_restarts": snapshot.capture_restarts,
                "capture_error": snapshot.capture_error,
                "processing_error": snapshot.processing_error,
                "last_classification": (
                    snapshot.last_decision.classification.value
                    if snapshot.last_decision is not None
                    else None
                ),
            },
            sort_keys=True,
        )
    )
    return 2 if snapshot.capture_error or snapshot.processing_error else 0


def _wave_audio(path: Path, *, session_id: str, start_ns: int) -> AudioChunk:
    with wave.open(str(path), "rb") as stream:
        if stream.getcomptype() != "NONE":
            raise ValueError("fingerprint WAV input must contain uncompressed PCM")
        widths = {1: "u8", 2: "s16", 4: "s32"}
        sample_format = widths.get(stream.getsampwidth())
        if sample_format is None:
            raise ValueError("fingerprint WAV input must use 8, 16, or 32-bit PCM")
        pcm = stream.readframes(stream.getnframes())
        return AudioChunk(
            stamp=MediaStamp(session_id, 0, 0, start_ns),
            sample_rate_hz=stream.getframerate(),
            channels=stream.getnchannels(),
            sample_format=sample_format,
            pcm=pcm,
        )


def _fingerprint_reference_payload(reference: FingerprintReference) -> dict[str, object]:
    return {
        "ad_id": str(reference.ad_id),
        "name": reference.name,
        "label_revision_id": reference.label_revision_id,
        "session_id": reference.session_id,
        "start_ns": reference.start_ns,
        "end_ns": reference.end_ns,
        "duration_ms": reference.duration_ms,
        "fingerprint_version": reference.fingerprint_version,
        "landmark_count": reference.landmark_count,
        "status": reference.status.value,
        "created_at_ns": reference.created_at_ns,
    }


async def _fingerprint_enroll(
    config_path: Path,
    wav_path: Path,
    *,
    name: str,
    label_revision_id: str,
    allow_duplicate: bool,
) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        label = store.label_revision(label_revision_id, effective_only=True)
        if label is None:
            raise ValueError("label revision is missing or superseded")
        audio = _wave_audio(wav_path, session_id=label.session_id, start_ns=label.start_ns)
        reference = await SQLiteFingerprintIndex(store, settings.fingerprint).enroll(
            TrustedAdSample(
                name=name,
                label_revision_id=label_revision_id,
                session_id=label.session_id,
                start_ns=label.start_ns,
                end_ns=label.end_ns,
                audio=(audio,),
            ),
            created_at_ns=time.time_ns(),
            allow_duplicate=allow_duplicate,
        )
    print(json.dumps(_fingerprint_reference_payload(reference), sort_keys=True))
    return 0


async def _fingerprint_query(config_path: Path, wav_path: Path) -> int:
    settings = load_settings(config_path)
    audio = _wave_audio(wav_path, session_id="fingerprint-query", start_ns=0)
    with SQLiteStore(settings.storage.database_path) as store:
        matches = await SQLiteFingerprintIndex(store, settings.fingerprint).query((audio,))
    print(
        json.dumps(
            [
                {
                    **asdict(match),
                    "ad_id": str(match.ad_id),
                }
                for match in matches
            ],
            sort_keys=True,
        )
    )
    return 0 if matches else 1


def _fingerprint_list(config_path: Path) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        references = store.fingerprints()
    print(
        json.dumps(
            [_fingerprint_reference_payload(reference) for reference in references],
            sort_keys=True,
        )
    )
    return 0


def _fingerprint_status(config_path: Path, ad_id: UUID, status: str) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        store.set_fingerprint_status(ad_id, FingerprintStatus(status))
    return 0


def _fingerprint_remove(config_path: Path, ad_id: UUID) -> int:
    settings = load_settings(config_path)
    with SQLiteStore(settings.storage.database_path) as store:
        store.remove_fingerprint(ad_id)
    return 0


async def _ml_train(
    config_path: Path,
    dataset_path: Path,
    output_directory: Path,
    *,
    split_seed: str,
) -> int:
    from ad_detect.detectors import SklearnModelDetector

    settings = load_settings(config_path)
    dataset = load_dataset_manifest(dataset_path)
    with SQLiteStore(":memory:") as source:
        import_dataset(source, dataset_path)
        base_windows = source.feature_windows()
        labels = source.labels(effective_only=True)
    aggregated = aggregate_feature_windows(
        base_windows,
        schema_version=settings.ml.aggregation_schema,
    )
    examples = label_feature_windows(aggregated, labels)
    split = grouped_session_split(
        [example.features.session_id for example in examples],
        seed=split_seed,
    )
    held_out = [item for item in examples if item.features.session_id in split.test]
    held_out_windows = [item.features for item in held_out]
    held_out_labels = [label for label in labels if label.session_id in split.test]
    candidates: list[dict[str, object]] = []
    output_directory.mkdir(parents=True, exist_ok=True)
    for modality in ModelModality:
        for algorithm in ModelAlgorithm:
            model = train_candidate(
                examples,
                split,
                modality=modality,
                algorithm=algorithm,
            )
            metrics = evaluate_candidate(model, held_out)
            bundle_path, bundle = save_model_bundle(
                model,
                output_directory,
                dataset_digest=dataset.digest,
                created_at_ns=time.time_ns(),
                metrics=metrics,
            )
            detector = SklearnModelDetector(
                model,
                model_digest=bundle.digest,
                inference_timeout_ms=settings.ml.inference_timeout_ms,
            )
            replay = await MLReplayEvaluator(detector).evaluate(
                held_out_windows,
                held_out_labels,
            )
            by_session: dict[str, object] = {}
            for session_id in sorted(split.test):
                session_windows = [
                    window for window in held_out_windows if window.session_id == session_id
                ]
                session_labels = [
                    label for label in held_out_labels if label.session_id == session_id
                ]
                by_session[session_id] = asdict(
                    (
                        await MLReplayEvaluator(detector).evaluate(
                            session_windows,
                            session_labels,
                        )
                    ).metrics
                )
            candidates.append(
                {
                    "modality": modality.value,
                    "algorithm": algorithm.value,
                    "bundle_digest": bundle.digest,
                    "bundle_path": str(bundle_path),
                    "calibration_method": bundle.calibration_method,
                    "classification_metrics": asdict(metrics),
                    "causal_replay_metrics": asdict(replay.metrics),
                    "session_metrics": by_session,
                }
            )
    rules_replay = await RulesReplayEvaluator(
        settings.rules,
        settings.detection,
        feature_schema=settings.features.schema_version,
        stale_after_ms=settings.capture.stale_after_ms,
        max_av_skew_ms=settings.capture.max_av_skew_ms,
        temporal=settings.temporal,
    ).evaluate(
        [window for window in base_windows if window.session_id in split.test],
        held_out_labels,
    )
    report = {
        "format_version": 1,
        "dataset_digest": dataset.digest,
        "split_seed": split_seed,
        "split": {
            "train": sorted(split.train),
            "calibration": sorted(split.calibration),
            "test": sorted(split.test),
        },
        "rules_causal_replay_metrics": asdict(rules_replay.metrics),
        "candidates": candidates,
        "accuracy_claim": None,
    }
    document = json.dumps(report, sort_keys=True, separators=(",", ":"))
    report_digest = hashlib.sha256(document.encode()).hexdigest()
    report_path = output_directory / f"{report_digest}.training-report.json"
    if not report_path.exists():
        report_path.write_text(document, encoding="utf-8")
    print(
        json.dumps(
            {
                "report_digest": report_digest,
                "report_path": str(report_path),
                "candidate_count": len(candidates),
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ad-detect")
    parser.add_argument("--config", type=Path, default=Path("configs/defaults.toml"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help="run the local dashboard/API")
    simulate = subparsers.add_parser("simulate", help="run deterministic detector scores")
    simulate.add_argument(
        "--scores",
        default="0.1,0.1,0.9,0.9,0.9,0.9,0.1,0.1,unknown,0.1",
        help="comma-separated commercial scores or 'unknown'",
    )
    subparsers.add_parser("capture-devices", help="list FFmpeg AVFoundation capture devices")
    subparsers.add_parser("capture-probe", help="test the configured audio/video capture pair")
    subparsers.add_parser("appletv-devices", help="discover Apple TV devices with pyatv")
    label = subparsers.add_parser("label-add", help="append a label or correction")
    label.add_argument("--session", required=True)
    label.add_argument("--start-ns", required=True, type=int)
    label.add_argument("--end-ns", required=True, type=int)
    label.add_argument("--label", required=True, choices=[item.value for item in Classification])
    label.add_argument(
        "--boundary", default=BoundaryKind.NONE.value, choices=[item.value for item in BoundaryKind]
    )
    label.add_argument("--confidence", type=float, default=1.0)
    label.add_argument("--author", required=True)
    label.add_argument("--supersedes")
    label.add_argument("--notes", default="")
    dataset_export = subparsers.add_parser(
        "dataset-export", help="export a content-addressed dataset manifest"
    )
    dataset_export.add_argument("--output", type=Path, default=Path("var/datasets"))
    dataset_export.add_argument("--session", action="append", default=[])
    dataset_import = subparsers.add_parser(
        "dataset-import", help="verify and import a dataset manifest"
    )
    dataset_import.add_argument("manifest", type=Path)
    replay = subparsers.add_parser("rules-replay", help="evaluate the causal rules baseline")
    replay.add_argument("--session")
    temporal_replay = subparsers.add_parser(
        "temporal-replay",
        help="compare hysteresis, sliding-window, and HMM causal replay",
    )
    temporal_replay.add_argument("--session")
    evaluation_run = subparsers.add_parser(
        "evaluation-run",
        help="create an immutable held-out evaluation report",
    )
    evaluation_run.add_argument("dataset", type=Path)
    evaluation_run.add_argument("--candidate", action="append", type=Path, default=[])
    evaluation_run.add_argument("--output", type=Path, default=Path("var/evaluations"))
    evaluation_run.add_argument("--split-seed", default="ad-detect-split-v1")
    model_register = subparsers.add_parser(
        "model-register",
        help="register an evaluated model without activating it",
    )
    model_register.add_argument("bundle", type=Path)
    model_register.add_argument("--evaluation", required=True)
    model_register.add_argument("--actor", required=True)
    model_register.add_argument("--notes", default="")
    subparsers.add_parser("model-list", help="list registered models and active status")
    capture_shadow = subparsers.add_parser(
        "capture-shadow",
        help="run FFmpeg capture through passive fusion without controlling playback",
    )
    capture_shadow.add_argument("--session")
    fingerprint_enroll = subparsers.add_parser(
        "fingerprint-enroll", help="enroll a reviewed commercial PCM WAV"
    )
    fingerprint_enroll.add_argument("wav", type=Path)
    fingerprint_enroll.add_argument("--name", required=True)
    fingerprint_enroll.add_argument("--label-revision", required=True)
    fingerprint_enroll.add_argument("--allow-duplicate", action="store_true")
    fingerprint_query = subparsers.add_parser(
        "fingerprint-query", help="query a PCM WAV against active fingerprints"
    )
    fingerprint_query.add_argument("wav", type=Path)
    subparsers.add_parser("fingerprint-list", help="list fingerprint references")
    fingerprint_status = subparsers.add_parser(
        "fingerprint-status", help="enable or disable a fingerprint"
    )
    fingerprint_status.add_argument("ad_id", type=UUID)
    fingerprint_status.add_argument("status", choices=[item.value for item in FingerprintStatus])
    fingerprint_remove = subparsers.add_parser(
        "fingerprint-remove", help="permanently remove a fingerprint"
    )
    fingerprint_remove.add_argument("ad_id", type=UUID)
    ml_train = subparsers.add_parser(
        "ml-train", help="train and compare inactive tabular ML candidates"
    )
    ml_train.add_argument("dataset", type=Path)
    ml_train.add_argument("--output", type=Path, default=Path("models/candidates"))
    ml_train.add_argument("--split-seed", default="ad-detect-split-v1")
    args = parser.parse_args()
    configure_logging()
    if args.command == "simulate":
        return asyncio.run(_simulate(args.config, args.scores))
    if args.command == "capture-devices":
        return asyncio.run(_capture_devices(args.config))
    if args.command == "capture-probe":
        return asyncio.run(_capture_probe(args.config))
    if args.command == "appletv-devices":
        return asyncio.run(_appletv_devices(args.config))
    if args.command == "label-add":
        return _add_label(
            args.config,
            session_id=args.session,
            start_ns=args.start_ns,
            end_ns=args.end_ns,
            label=args.label,
            boundary=args.boundary,
            confidence=args.confidence,
            author=args.author,
            supersedes_id=args.supersedes,
            notes=args.notes,
        )
    if args.command == "dataset-export":
        return _export_dataset(args.config, args.output, args.session)
    if args.command == "dataset-import":
        return _import_dataset(args.config, args.manifest)
    if args.command == "rules-replay":
        return asyncio.run(_rules_replay(args.config, args.session))
    if args.command == "temporal-replay":
        return asyncio.run(_temporal_replay(args.config, args.session))
    if args.command == "evaluation-run":
        return asyncio.run(
            _evaluation_run(
                args.config,
                args.dataset,
                args.candidate,
                args.output,
                split_seed=args.split_seed,
            )
        )
    if args.command == "model-register":
        return _model_register(
            args.config,
            args.bundle,
            args.evaluation,
            actor=args.actor,
            notes=args.notes,
        )
    if args.command == "model-list":
        return _model_list(args.config)
    if args.command == "capture-shadow":
        return asyncio.run(_capture_shadow(args.config, args.session))
    if args.command == "fingerprint-enroll":
        return asyncio.run(
            _fingerprint_enroll(
                args.config,
                args.wav,
                name=args.name,
                label_revision_id=args.label_revision,
                allow_duplicate=args.allow_duplicate,
            )
        )
    if args.command == "fingerprint-query":
        return asyncio.run(_fingerprint_query(args.config, args.wav))
    if args.command == "fingerprint-list":
        return _fingerprint_list(args.config)
    if args.command == "fingerprint-status":
        return _fingerprint_status(args.config, args.ad_id, args.status)
    if args.command == "fingerprint-remove":
        return _fingerprint_remove(args.config, args.ad_id)
    if args.command == "ml-train":
        return asyncio.run(
            _ml_train(
                args.config,
                args.dataset,
                args.output,
                split_seed=args.split_seed,
            )
        )

    settings = load_settings(args.config)
    import uvicorn

    uvicorn.run(
        create_app(settings),
        host=settings.server.host,
        port=settings.server.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
