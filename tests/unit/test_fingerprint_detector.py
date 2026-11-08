from uuid import UUID

import pytest

from ad_detect.config.settings import FingerprintSettings
from ad_detect.detectors import FingerprintDetector
from ad_detect.domain import (
    AudioChunk,
    CaptureHealth,
    Classification,
    FingerprintMatch,
    MediaStamp,
)

AD_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _match(position_ms: int, *, ad_id: UUID = AD_ID) -> FingerprintMatch:
    return FingerprintMatch(
        ad_id=ad_id,
        name="Known ad",
        confidence=0.9,
        aligned_hashes=20,
        query_hashes=100,
        playback_position_ms=position_ms,
        offset_dispersion_ms=5.0,
        runner_up_margin=3.0,
        reference_version="landmark-v1",
    )


class FakeIndex:
    def __init__(self, results: list[tuple[FingerprintMatch, ...]]) -> None:
        self.results = results

    async def query(self, audio: tuple[AudioChunk, ...]) -> tuple[FingerprintMatch, ...]:
        assert audio
        return self.results.pop(0)


def _audio() -> tuple[AudioChunk, ...]:
    return (AudioChunk(MediaStamp("s", 0, 0, 0), 16_000, 1, "s16", b"\x00\x00"),)


def _health(*, valid: bool = True) -> CaptureHealth:
    return CaptureHealth(
        at_ns=1,
        video_valid=valid,
        audio_valid=valid,
        stale_ms=0,
        reasons=() if valid else ("capture unavailable",),
    )


@pytest.mark.asyncio
async def test_match_requires_identity_and_position_continuity() -> None:
    detector = FingerprintDetector(
        FakeIndex([(_match(1_000),), (_match(2_000),)]),
        FingerprintSettings(continuity_ticks=2),
    )

    first = await detector.infer(
        _audio(), window_start_ns=0, window_end_ns=1_000_000_000, health=_health()
    )
    second = await detector.infer(
        _audio(),
        window_start_ns=1_000_000_000,
        window_end_ns=2_000_000_000,
        health=_health(),
    )

    assert first.probabilities[Classification.UNKNOWN] == 1.0
    assert second.probabilities[Classification.COMMERCIAL] == 0.9
    assert second.metadata["continuity_ticks"] == 2


@pytest.mark.asyncio
async def test_position_jump_and_unhealthy_capture_reset_continuity() -> None:
    detector = FingerprintDetector(
        FakeIndex([(_match(1_000),), (_match(9_000),), (_match(10_000),)]),
        FingerprintSettings(continuity_ticks=2, continuity_position_tolerance_ms=100),
    )
    first = await detector.infer(
        _audio(), window_start_ns=0, window_end_ns=1_000_000_000, health=_health()
    )
    jump = await detector.infer(
        _audio(),
        window_start_ns=1_000_000_000,
        window_end_ns=2_000_000_000,
        health=_health(),
    )
    unhealthy = await detector.infer(
        _audio(),
        window_start_ns=2_000_000_000,
        window_end_ns=3_000_000_000,
        health=_health(valid=False),
    )

    assert first.probabilities[Classification.UNKNOWN] == 1.0
    assert jump.probabilities[Classification.UNKNOWN] == 1.0
    assert not unhealthy.valid
    assert unhealthy.probabilities[Classification.COMMERCIAL] == 0.0
