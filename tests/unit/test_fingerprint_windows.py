from ad_detect.domain import AudioChunk, CaptureHealth, MediaStamp, SynchronizedWindow
from ad_detect.fingerprint import RollingFingerprintWindows

SECOND = 1_000_000_000


def _window(index: int, *, valid: bool = True) -> SynchronizedWindow:
    start = index * SECOND
    return SynchronizedWindow(
        session_id="s",
        start_ns=start,
        end_ns=start + SECOND,
        audio=(AudioChunk(MediaStamp("s", index, start, start), 16_000, 1, "s16", b"xx"),),
        video=(),
        health=CaptureHealth(
            at_ns=start + SECOND,
            video_valid=valid,
            audio_valid=valid,
            stale_ms=0,
            reasons=() if valid else ("capture gap",),
        ),
    )


def test_rolling_fingerprint_window_is_causal_and_resets_on_gap() -> None:
    windows = RollingFingerprintWindows(duration_ns=3 * SECOND)

    assert windows.push(_window(0)) is None
    assert windows.push(_window(1)) is None
    query = windows.push(_window(2))
    assert query is not None
    assert query.start_ns == 0
    assert query.end_ns == 3 * SECOND
    assert len(query.audio) == 3

    assert windows.push(_window(3, valid=False)) is None
    assert windows.push(_window(4)) is None
    assert windows.push(_window(5)) is None
    resumed = windows.push(_window(6))
    assert resumed is not None
    assert resumed.start_ns == 4 * SECOND
