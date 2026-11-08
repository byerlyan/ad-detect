"""Causal rolling audio windows for live fingerprint queries."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ad_detect.domain import AudioChunk, CaptureHealth, SynchronizedWindow


@dataclass(frozen=True, slots=True)
class FingerprintQueryWindow:
    start_ns: int
    end_ns: int
    audio: tuple[AudioChunk, ...]
    health: CaptureHealth


class RollingFingerprintWindows:
    def __init__(self, *, duration_ns: int) -> None:
        if duration_ns <= 0:
            raise ValueError("fingerprint query duration must be positive")
        self.duration_ns = duration_ns
        self._windows: deque[SynchronizedWindow] = deque()

    def reset(self) -> None:
        self._windows.clear()

    def push(self, window: SynchronizedWindow) -> FingerprintQueryWindow | None:
        if not window.health.valid or not window.audio:
            self.reset()
            return None
        if self._windows and window.start_ns != self._windows[-1].end_ns:
            self.reset()
        self._windows.append(window)
        cutoff = window.end_ns - self.duration_ns
        while self._windows and self._windows[0].end_ns <= cutoff:
            self._windows.popleft()
        if not self._windows or window.end_ns - self._windows[0].start_ns < self.duration_ns:
            return None
        return FingerprintQueryWindow(
            start_ns=max(cutoff, self._windows[0].start_ns),
            end_ns=window.end_ns,
            audio=tuple(chunk for item in self._windows for chunk in item.audio),
            health=window.health,
        )
