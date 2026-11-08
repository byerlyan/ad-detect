"""Safe live capture composition for the rules-only shadow baseline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from ad_detect.capture import (
    CaptureHealthMonitor,
    CaptureSupervisor,
    CaptureSupervisorState,
    DropOldestChannel,
    FFmpegAVFoundation,
    MediaRecord,
)
from ad_detect.config import Settings
from ad_detect.data import SQLiteStore
from ad_detect.detectors import RulesDetector
from ad_detect.domain import Decision, DetectorEvidence, OperatingMode
from ad_detect.features import ClassicalFeatureExtractor, WindowAssembler
from ad_detect.intervention import SimulatedIntervention

from .runtime import DetectionRuntime


class CaptureSource(Protocol):
    def subscribe(
        self, name: str, capacity: int | None = None
    ) -> DropOldestChannel[MediaRecord]: ...

    async def start(self) -> None: ...

    async def wait(self) -> None: ...

    async def stop(self) -> None: ...

    def state(self) -> CaptureSupervisorState: ...


@dataclass(frozen=True, slots=True)
class ProcessorSnapshot:
    records_processed: int
    windows_processed: int
    last_decision: Decision | None


@dataclass(frozen=True, slots=True)
class LivePipelineSnapshot:
    running: bool
    records_processed: int
    windows_processed: int
    queue_drops: int
    capture_restarts: int
    capture_error: str | None
    processing_error: str | None
    last_decision: Decision | None


def evidence_payload(evidence: DetectorEvidence) -> dict[str, object]:
    return {
        "detector_id": evidence.detector_id,
        "detector_version": evidence.detector_version,
        "window_start_ns": evidence.window_start_ns,
        "window_end_ns": evidence.window_end_ns,
        "probabilities": {
            classification.value: probability
            for classification, probability in evidence.probabilities.items()
        },
        "valid": evidence.valid,
        "latency_ms": evidence.latency_ms,
        "explanation": list(evidence.explanation),
        "metadata": dict(evidence.metadata),
    }


class FeatureDecisionProcessor:
    """Turn ordered decoded media into persisted causal shadow decisions."""

    def __init__(self, settings: Settings, store: SQLiteStore, *, session_id: str) -> None:
        safe_intervention = settings.intervention.model_copy(
            update={
                "mode": OperatingMode.SHADOW,
                "automatic_muting": False,
                "adapter": "simulated",
            }
        )
        safe_settings = settings.model_copy(update={"intervention": safe_intervention})
        self.store = store
        self.session_id = session_id
        self._assembler = WindowAssembler(
            session_id=session_id,
            duration_ns=settings.detection.tick_interval_ms * 1_000_000,
            health_monitor=self._health_monitor(settings),
        )
        self._extractor = ClassicalFeatureExtractor(settings.features)
        self._detector = RulesDetector(
            settings.rules,
            feature_schema=settings.features.schema_version,
        )
        self._runtime = DetectionRuntime(
            safe_settings,
            store,
            session_id=session_id,
            intervention=SimulatedIntervention(),
        )
        self._records_processed = 0
        self._windows_processed = 0
        self._last_decision: Decision | None = None
        self._started = False

    @staticmethod
    def _health_monitor(settings: Settings) -> CaptureHealthMonitor:
        return CaptureHealthMonitor(
            stale_after_ms=settings.capture.stale_after_ms,
            max_av_skew_ms=settings.capture.max_av_skew_ms,
        )

    async def start(self) -> None:
        if not self._started:
            await self._runtime.start()
            self._started = True

    async def stop(self) -> None:
        if self._started:
            await self._runtime.stop()
            self._started = False

    def mark_gap(self, *, at_ns: int, reason: str) -> None:
        self._assembler.mark_gap(reason)
        self._extractor.reset()
        self.store.append_event(
            session_id=self.session_id,
            at_ns=at_ns,
            kind="capture.gap",
            payload={"reason": reason},
        )

    async def process(self, record: MediaRecord) -> tuple[Decision, ...]:
        if not self._started:
            raise RuntimeError("feature decision processor is not running")
        if record.stamp.session_id != self.session_id:
            raise ValueError("media record belongs to a different session")
        if record.stamp.discontinuity:
            self.mark_gap(
                at_ns=record.stamp.captured_monotonic_ns,
                reason="capture timestamp discontinuity",
            )
        self._records_processed += 1
        decisions: list[Decision] = []
        for window in self._assembler.push(record):
            features = self._extractor.extract(window)
            self.store.save_feature_window(features)
            evidence = await self._detector.infer(features)
            self.store.append_event(
                session_id=self.session_id,
                at_ns=window.end_ns,
                kind="detector.evidence",
                payload=evidence_payload(evidence),
            )
            decision = await self._runtime.process_evidence((evidence,), window.health)
            self._windows_processed += 1
            self._last_decision = decision
            decisions.append(decision)
        return tuple(decisions)

    def snapshot(self) -> ProcessorSnapshot:
        return ProcessorSnapshot(
            records_processed=self._records_processed,
            windows_processed=self._windows_processed,
            last_decision=self._last_decision,
        )


class LiveShadowPipeline:
    """Own one capture source and consume it through the safe shadow processor."""

    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        *,
        session_id: str,
        source: CaptureSource | None = None,
    ) -> None:
        self.source = source or CaptureSupervisor(
            FFmpegAVFoundation(settings.capture),
            session_id=session_id,
            queue_capacity=settings.capture.queue_capacity,
        )
        self.processor = FeatureDecisionProcessor(settings, store, session_id=session_id)
        self._channel = self.source.subscribe("shadow-features", settings.capture.queue_capacity)
        self._consumer: asyncio.Task[None] | None = None
        self._running = False
        self._seen_drops = 0
        self._processing_error: str | None = None
        self._idle = asyncio.Event()
        self._idle.set()

    async def start(self) -> None:
        if self._running:
            return
        await self.processor.start()
        self._running = True
        self._consumer = asyncio.create_task(self._consume(), name="live-shadow-features")
        try:
            await self.source.start()
        except Exception:
            await self.stop()
            raise

    async def _consume(self) -> None:
        while self._running:
            record = await self._channel.receive()
            self._idle.clear()
            try:
                stats = self._channel.stats
                if stats.dropped > self._seen_drops:
                    dropped = stats.dropped - self._seen_drops
                    self._seen_drops = stats.dropped
                    self.processor.mark_gap(
                        at_ns=record.stamp.captured_monotonic_ns,
                        reason=f"feature queue dropped {dropped} media records",
                    )
                await self.processor.process(record)
            except Exception as exc:
                self._processing_error = str(exc)
                self.processor.mark_gap(
                    at_ns=record.stamp.captured_monotonic_ns,
                    reason=f"media processing failed: {exc}",
                )
            finally:
                self._idle.set()

    async def wait(self) -> None:
        await self.source.wait()
        while True:
            await asyncio.sleep(0)
            if self._channel.stats.depth == 0 and self._idle.is_set():
                break

    async def stop(self) -> None:
        if not self._running and self._consumer is None:
            return
        self._running = False
        await self.source.stop()
        if self._consumer is not None:
            self._consumer.cancel()
            await asyncio.gather(self._consumer, return_exceptions=True)
            self._consumer = None
        await self.processor.stop()

    def snapshot(self) -> LivePipelineSnapshot:
        capture = self.source.state()
        processor = self.processor.snapshot()
        return LivePipelineSnapshot(
            running=self._running,
            records_processed=processor.records_processed,
            windows_processed=processor.windows_processed,
            queue_drops=self._channel.stats.dropped,
            capture_restarts=capture.restart_count,
            capture_error=capture.last_error,
            processing_error=self._processing_error,
            last_decision=processor.last_decision,
        )
