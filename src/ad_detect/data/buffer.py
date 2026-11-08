"""Duration- and memory-bounded decoded-media ring buffer."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ad_detect.capture import MediaRecord
from ad_detect.domain import AudioChunk


@dataclass(frozen=True, slots=True)
class BufferStats:
    records: int
    bytes_used: int
    evicted_records: int
    oldest_ns: int | None
    newest_ns: int | None


def _record_size(record: MediaRecord) -> int:
    return len(record.pcm) if isinstance(record, AudioChunk) else len(record.pixels)


class RollingMediaBuffer:
    def __init__(self, *, duration_ns: int, max_bytes: int) -> None:
        if duration_ns <= 0 or max_bytes <= 0:
            raise ValueError("rolling-buffer limits must be positive")
        self.duration_ns = duration_ns
        self.max_bytes = max_bytes
        self._records: deque[MediaRecord] = deque()
        self._bytes = 0
        self._evicted = 0

    def append(self, record: MediaRecord) -> None:
        at_ns = record.stamp.captured_monotonic_ns
        if self._records and at_ns < self._records[-1].stamp.captured_monotonic_ns:
            raise ValueError("rolling-buffer records must be time ordered")
        self._records.append(record)
        self._bytes += _record_size(record)
        cutoff = at_ns - self.duration_ns
        while self._records and (
            self._records[0].stamp.captured_monotonic_ns < cutoff or self._bytes > self.max_bytes
        ):
            removed = self._records.popleft()
            self._bytes -= _record_size(removed)
            self._evicted += 1

    def span(self, start_ns: int, end_ns: int) -> tuple[MediaRecord, ...]:
        if end_ns <= start_ns:
            raise ValueError("span end must follow start")
        return tuple(
            record
            for record in self._records
            if start_ns <= record.stamp.captured_monotonic_ns < end_ns
        )

    def stats(self) -> BufferStats:
        return BufferStats(
            records=len(self._records),
            bytes_used=self._bytes,
            evicted_records=self._evicted,
            oldest_ns=(self._records[0].stamp.captured_monotonic_ns if self._records else None),
            newest_ns=(self._records[-1].stamp.captured_monotonic_ns if self._records else None),
        )
