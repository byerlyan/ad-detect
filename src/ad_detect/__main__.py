"""Command-line entry points for the local server and deterministic demo."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from ad_detect.api import create_app
from ad_detect.app import DetectionRuntime
from ad_detect.capture import SimulatedCapture
from ad_detect.capture.ffmpeg import FFmpegAVFoundation
from ad_detect.config import load_settings
from ad_detect.data import SQLiteStore
from ad_detect.domain import BoundaryKind, Classification
from ad_detect.learning import RulesReplayEvaluator, export_dataset, import_dataset
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
        )
        result = await evaluator.evaluate(
            store.feature_windows(session_id=session_id),
            store.labels(session_id=session_id, effective_only=True),
        )
    print(json.dumps(asdict(result.metrics), sort_keys=True))
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
