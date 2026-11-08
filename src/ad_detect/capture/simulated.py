"""Deterministic input used by tests, replay, and first-run demonstrations."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from ad_detect.domain import CaptureHealth, DetectorEvidence
from ad_detect.domain.models import probabilities


@dataclass(frozen=True, slots=True)
class SimulatedTick:
    at_ns: int
    health: CaptureHealth
    evidence: tuple[DetectorEvidence, ...]


class SimulatedCapture:
    """Yield a caller-supplied score schedule on a fixed monotonic clock."""

    def __init__(
        self,
        commercial_scores: Iterable[float | None],
        *,
        start_ns: int = 1_000_000_000,
        tick_ns: int = 1_000_000_000,
    ) -> None:
        if tick_ns <= 0:
            raise ValueError("tick_ns must be positive")
        self._scores = tuple(commercial_scores)
        self._start_ns = start_ns
        self._tick_ns = tick_ns

    def __iter__(self) -> Iterator[SimulatedTick]:
        for index, score in enumerate(self._scores):
            at_ns = self._start_ns + index * self._tick_ns
            if score is None:
                health = CaptureHealth(
                    at_ns=at_ns,
                    video_valid=False,
                    audio_valid=False,
                    stale_ms=2_000.0,
                    reasons=("simulated capture unavailable",),
                )
                evidence = DetectorEvidence(
                    detector_id="simulated",
                    detector_version="1",
                    window_start_ns=at_ns - self._tick_ns,
                    window_end_ns=at_ns,
                    probabilities=probabilities(0.0, 0.0, 1.0),
                    valid=False,
                    explanation=("capture unavailable",),
                )
            else:
                if not 0.0 <= score <= 1.0:
                    raise ValueError("simulated commercial score must be in [0, 1]")
                health = CaptureHealth(
                    at_ns=at_ns,
                    video_valid=True,
                    audio_valid=True,
                    stale_ms=0.0,
                    av_skew_ms=0.0,
                )
                evidence = DetectorEvidence(
                    detector_id="simulated",
                    detector_version="1",
                    window_start_ns=at_ns - self._tick_ns,
                    window_end_ns=at_ns,
                    probabilities=probabilities(1.0 - score, score, 0.0),
                    valid=True,
                    explanation=(f"simulated commercial score={score:.3f}",),
                    metadata={"sequence": index},
                )
            yield SimulatedTick(at_ns=at_ns, health=health, evidence=(evidence,))
