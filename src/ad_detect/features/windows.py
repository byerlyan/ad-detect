"""Assemble decoded media records into causal synchronized time windows."""

from __future__ import annotations

from ad_detect.capture import CaptureHealthMonitor, MediaHealthConsumer, MediaRecord
from ad_detect.domain import AudioChunk, SynchronizedWindow, VideoFrame


class WindowAssembler:
    def __init__(
        self,
        *,
        session_id: str,
        duration_ns: int,
        health_monitor: CaptureHealthMonitor,
    ) -> None:
        if duration_ns <= 0:
            raise ValueError("window duration must be positive")
        self.session_id = session_id
        self.duration_ns = duration_ns
        self._health = health_monitor
        self._health_consumer = MediaHealthConsumer(health_monitor)
        self._start_ns: int | None = None
        self._audio: list[AudioChunk] = []
        self._video: list[VideoFrame] = []

    def _emit(self) -> SynchronizedWindow:
        if self._start_ns is None:
            raise RuntimeError("window assembler has not started")
        end_ns = self._start_ns + self.duration_ns
        result = SynchronizedWindow(
            session_id=self.session_id,
            start_ns=self._start_ns,
            end_ns=end_ns,
            audio=tuple(self._audio),
            video=tuple(self._video),
            health=self._health.snapshot(end_ns),
        )
        self._audio.clear()
        self._video.clear()
        self._start_ns = end_ns
        return result

    def push(self, record: MediaRecord) -> tuple[SynchronizedWindow, ...]:
        at_ns = record.stamp.captured_monotonic_ns
        if self._start_ns is None:
            self._start_ns = at_ns - at_ns % self.duration_ns
        emitted: list[SynchronizedWindow] = []
        while at_ns >= self._start_ns + self.duration_ns:
            emitted.append(self._emit())
        if isinstance(record, AudioChunk):
            self._audio.append(record)
        else:
            self._video.append(record)
        self._health_consumer.observe(record)
        return tuple(emitted)

    def flush(self, until_ns: int) -> tuple[SynchronizedWindow, ...]:
        if self._start_ns is None:
            return ()
        emitted: list[SynchronizedWindow] = []
        while until_ns >= self._start_ns + self.duration_ns:
            emitted.append(self._emit())
        return tuple(emitted)
