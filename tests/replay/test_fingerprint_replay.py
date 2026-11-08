from uuid import UUID

import pytest

from ad_detect.config.settings import FingerprintSettings
from ad_detect.domain import (
    AudioChunk,
    BoundaryKind,
    CaptureHealth,
    Classification,
    FingerprintMatch,
    LabelRevision,
    MediaStamp,
    SynchronizedWindow,
)
from ad_detect.learning import FingerprintReplayEvaluator

SECOND = 1_000_000_000


def _match(position_ms: int) -> FingerprintMatch:
    return FingerprintMatch(
        ad_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        name="Known ad",
        confidence=0.9,
        aligned_hashes=20,
        query_hashes=100,
        playback_position_ms=position_ms,
        offset_dispersion_ms=5,
        runner_up_margin=3,
        reference_version="landmark-v1",
    )


class FakeIndex:
    def __init__(self) -> None:
        self.position = 5_000

    async def query(self, audio: tuple[AudioChunk, ...]) -> tuple[FingerprintMatch, ...]:
        assert audio
        match = _match(self.position)
        self.position += 1_000
        return (match,)


def _window(index: int) -> SynchronizedWindow:
    start = index * SECOND
    return SynchronizedWindow(
        session_id="session",
        start_ns=start,
        end_ns=start + 5 * SECOND,
        audio=(AudioChunk(MediaStamp("session", index, start, start), 16_000, 1, "s16", b"xx"),),
        video=(),
        health=CaptureHealth(
            at_ns=start + 5 * SECOND,
            video_valid=True,
            audio_valid=True,
            stale_ms=0,
        ),
    )


@pytest.mark.asyncio
async def test_fingerprint_replay_scores_only_post_window_actions() -> None:
    label = LabelRevision(
        revision_id="label",
        session_id="session",
        start_ns=0,
        end_ns=10 * SECOND,
        label=Classification.COMMERCIAL,
        boundary=BoundaryKind.NONE,
        confidence=1,
        author="fixture",
        created_at_ns=1,
    )
    evaluator = FingerprintReplayEvaluator(FakeIndex(), FingerprintSettings(continuity_ticks=2))

    result = await evaluator.evaluate([_window(0), _window(1), _window(2)], [label])

    assert [prediction.classification for prediction in result.predictions] == [
        Classification.UNKNOWN,
        Classification.COMMERCIAL,
        Classification.COMMERCIAL,
    ]
    assert result.metrics.labeled_seconds == 2.0
    assert result.metrics.commercial_mute_coverage == 0.5
