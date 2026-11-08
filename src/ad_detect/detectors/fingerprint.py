"""Continuity-guarded fingerprint matches as three-state detector evidence."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ad_detect.config.settings import FingerprintSettings
from ad_detect.domain import (
    AudioChunk,
    CaptureHealth,
    DetectorEvidence,
    FingerprintMatch,
)
from ad_detect.domain.models import probabilities


class FingerprintQueryIndex(Protocol):
    async def query(self, audio: tuple[AudioChunk, ...]) -> tuple[FingerprintMatch, ...]: ...


class FingerprintDetector:
    def __init__(self, index: FingerprintQueryIndex, settings: FingerprintSettings) -> None:
        self.index = index
        self.settings = settings
        self._candidate: UUID | None = None
        self._ticks = 0
        self._position_ms: int | None = None
        self._window_end_ns: int | None = None

    def reset(self) -> None:
        self._candidate = None
        self._ticks = 0
        self._position_ms = None
        self._window_end_ns = None

    async def infer(
        self,
        audio: tuple[AudioChunk, ...],
        *,
        window_start_ns: int,
        window_end_ns: int,
        health: CaptureHealth,
    ) -> DetectorEvidence:
        if not health.valid or not audio:
            self.reset()
            return self._unknown(
                window_start_ns,
                window_end_ns,
                "capture health prevents fingerprint matching",
                valid=False,
            )
        matches = await self.index.query(audio)
        if not matches:
            self.reset()
            return self._unknown(
                window_start_ns,
                window_end_ns,
                "no accepted fingerprint match",
                valid=True,
            )
        match = matches[0]
        continuous = self._is_continuous(match, window_end_ns)
        if match.ad_id == self._candidate and continuous:
            self._ticks += 1
        else:
            self._candidate = match.ad_id
            self._ticks = 1
        self._position_ms = match.playback_position_ms
        self._window_end_ns = window_end_ns
        if self._ticks < self.settings.continuity_ticks:
            return self._unknown(
                window_start_ns,
                window_end_ns,
                "fingerprint match awaits continuity confirmation",
                valid=True,
                match=match,
            )
        return DetectorEvidence(
            detector_id="audio-fingerprint",
            detector_version=self.settings.version,
            window_start_ns=window_start_ns,
            window_end_ns=window_end_ns,
            probabilities=probabilities(0.0, match.confidence, 1.0 - match.confidence),
            valid=True,
            explanation=(
                f"matched {match.name}",
                f"identity and position continuous for {self._ticks} windows",
            ),
            metadata=self._metadata(match),
        )

    def _is_continuous(self, match: FingerprintMatch, window_end_ns: int) -> bool:
        if (
            match.ad_id != self._candidate
            or self._position_ms is None
            or self._window_end_ns is None
        ):
            return False
        expected_advance_ms = (window_end_ns - self._window_end_ns) / 1_000_000
        actual_advance_ms = match.playback_position_ms - self._position_ms
        return (
            actual_advance_ms >= 0
            and abs(actual_advance_ms - expected_advance_ms)
            <= self.settings.continuity_position_tolerance_ms
        )

    def _unknown(
        self,
        start_ns: int,
        end_ns: int,
        reason: str,
        *,
        valid: bool,
        match: FingerprintMatch | None = None,
    ) -> DetectorEvidence:
        return DetectorEvidence(
            detector_id="audio-fingerprint",
            detector_version=self.settings.version,
            window_start_ns=start_ns,
            window_end_ns=end_ns,
            probabilities=probabilities(0.0, 0.0, 1.0),
            valid=valid,
            explanation=(reason,),
            metadata=self._metadata(match) if match is not None else {},
        )

    def _metadata(self, match: FingerprintMatch) -> dict[str, str | int | float | bool]:
        return {
            "ad_id": str(match.ad_id),
            "ad_name": match.name,
            "aligned_hashes": match.aligned_hashes,
            "query_hashes": match.query_hashes,
            "playback_position_ms": match.playback_position_ms,
            "offset_dispersion_ms": match.offset_dispersion_ms,
            "runner_up_margin": match.runner_up_margin,
            "continuity_ticks": self._ticks,
        }
