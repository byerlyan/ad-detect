import struct
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ad_detect.app import FeatureDecisionProcessor, LiveShadowPipeline
from ad_detect.capture import CaptureSupervisorState, DropOldestChannel, MediaRecord
from ad_detect.config import Settings, load_settings
from ad_detect.data import SQLiteStore
from ad_detect.domain import (
    AudioChunk,
    Classification,
    MediaStamp,
    OperatingMode,
    RecommendedAction,
    VideoFrame,
)

SECOND = 1_000_000_000


def _settings(*, queue_capacity: int = 8) -> Settings:
    document = load_settings().model_dump(mode="python")
    document["capture"]["queue_capacity"] = queue_capacity
    document["detection"]["enter_ticks"] = 1
    document["intervention"] = {
        "mode": OperatingMode.AUTOMATIC,
        "automatic_muting": True,
    }
    return Settings.model_validate(document)


def _stamp(sequence: int, at_ns: int, *, discontinuity: bool = False) -> MediaStamp:
    return MediaStamp("live-test", sequence, at_ns, at_ns, discontinuity)


def _audio(sequence: int, at_ns: int, *, invert: bool = False) -> AudioChunk:
    values = [16_000 if (index % 2 == 0) ^ invert else -16_000 for index in range(400)]
    pcm = struct.pack(f"<{len(values)}h", *values)
    return AudioChunk(_stamp(sequence, at_ns), 48_000, 2, "s16", pcm)


def _video(sequence: int, at_ns: int, *, invert: bool = False) -> VideoFrame:
    red = b"\x00\x00\xff"
    blue = b"\xff\x00\x00"
    pixels = (blue + red + red + blue) if invert else (red + blue + blue + red)
    return VideoFrame(_stamp(sequence, at_ns), 2, 2, "bgr24", pixels)


@pytest.mark.asyncio
async def test_feature_processor_persists_causal_shadow_decisions_and_gaps() -> None:
    with SQLiteStore(":memory:") as store:
        processor = FeatureDecisionProcessor(_settings(), store, session_id="live-test")
        await processor.start()
        records: Sequence[MediaRecord] = (
            _audio(0, 100_000_000),
            _video(1, 200_000_000),
            _audio(2, 1_100_000_000, invert=True),
            _video(3, 1_200_000_000, invert=True),
            _audio(4, 2_100_000_000),
            _video(5, 2_200_000_000),
        )
        decisions = []
        for record in records:
            decisions.extend(await processor.process(record))

        processor.mark_gap(at_ns=2_500_000_000, reason="test queue gap")
        decisions.extend(await processor.process(_audio(6, 3_100_000_000, invert=True)))
        await processor.stop()

        assert len(store.feature_windows(session_id="live-test")) == 3
        assert store.feature_windows(session_id="live-test")[-1].missing >= {"capture"}
        assert decisions[-1].classification is Classification.UNKNOWN
        assert all(decision.action is RecommendedAction.NONE for decision in decisions)
        events = store.timeline(session_id="live-test")
        assert sum(event.kind == "detector.evidence" for event in events) == 3
        assert sum(event.kind == "decision" for event in events) == 3
        assert any(event.kind == "capture.gap" for event in events)


@pytest.mark.asyncio
async def test_feature_processor_fuses_ml_evidence_in_shadow_without_actuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeModel:
        aggregation_schema = "aggregate-v1:classical-v1"
        columns = ("w1.audio.rms.mean",)
        classes = tuple(item.value for item in Classification)
        modality = SimpleNamespace(value="multimodal")
        algorithm = SimpleNamespace(value="test-model")
        calibration_method = "test"

        def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
            return np.tile(np.asarray([[0.0, 1.0, 0.0]]), (len(matrix), 1))

    monkeypatch.setattr(
        "ad_detect.app.live.load_model_bundle",
        lambda path, expected_feature_schema: (
            FakeModel(),
            SimpleNamespace(digest="model-digest"),
        ),
    )
    document = _settings().model_dump(mode="python")
    document["ml"] = {
        "enabled": True,
        "aggregation_schema": "aggregate-v1",
        "inference_timeout_ms": 200,
        "active_model_path": Path("models/candidates/example"),
    }
    settings = Settings.model_validate(document)

    with SQLiteStore(":memory:") as store:
        processor = FeatureDecisionProcessor(settings, store, session_id="live-test")
        await processor.start()
        decisions = []
        for record in (
            _audio(0, 100_000_000),
            _video(1, 200_000_000),
            _audio(2, 1_100_000_000),
            _video(3, 1_200_000_000),
            _audio(4, 2_100_000_000),
            _video(5, 2_200_000_000),
        ):
            decisions.extend(await processor.process(record))
        await processor.stop()

        evidence_events = [
            event
            for event in store.timeline(session_id="live-test")
            if event.kind == "detector.evidence"
        ]
        decision_events = [
            event for event in store.timeline(session_id="live-test") if event.kind == "decision"
        ]

        assert len(evidence_events) == 4
        assert (
            sum(event.payload["detector_id"] == "ml-multimodal" for event in evidence_events) == 2
        )
        assert len(decision_events) == 2
        assert any("ml-multimodal contribution" in reason for reason in decisions[-1].reasons)
        assert all(decision.action is RecommendedAction.NONE for decision in decisions)


class FakeCaptureSource:
    def __init__(self, records: Sequence[MediaRecord]) -> None:
        self.records = records
        self.channel: DropOldestChannel[MediaRecord] | None = None
        self.stopped = False

    def subscribe(self, name: str, capacity: int | None = None) -> DropOldestChannel[MediaRecord]:
        del name
        self.channel = DropOldestChannel(capacity or 1)
        return self.channel

    async def start(self) -> None:
        assert self.channel is not None
        for record in self.records:
            self.channel.publish_nowait(record)

    async def wait(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True

    def state(self) -> CaptureSupervisorState:
        return CaptureSupervisorState(False, 0, None)


@pytest.mark.asyncio
async def test_live_pipeline_surfaces_bounded_queue_drops() -> None:
    source = FakeCaptureSource(
        (
            _audio(0, 100_000_000),
            _video(1, 200_000_000),
            _audio(2, 1_100_000_000),
            _video(3, 1_200_000_000),
        )
    )
    with SQLiteStore(":memory:") as store:
        pipeline = LiveShadowPipeline(
            _settings(queue_capacity=2),
            store,
            session_id="live-test",
            source=source,
        )
        await pipeline.start()
        await pipeline.wait()
        snapshot = pipeline.snapshot()
        await pipeline.stop()

        assert snapshot.queue_drops == 2
        assert snapshot.processing_error is None
        gaps = [
            event for event in store.timeline(session_id="live-test") if event.kind == "capture.gap"
        ]
        assert len(gaps) == 1
        assert "dropped 2" in str(gaps[0].payload["reason"])
        assert source.stopped
