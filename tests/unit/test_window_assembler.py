from ad_detect.capture import CaptureHealthMonitor
from ad_detect.domain import AudioChunk, MediaStamp, VideoFrame
from ad_detect.features import WindowAssembler


def stamp(sequence: int, at_ns: int) -> MediaStamp:
    return MediaStamp("session", sequence, at_ns, at_ns)


def test_assembler_emits_aligned_windows_and_explicit_gaps() -> None:
    health = CaptureHealthMonitor(stale_after_ms=1500, max_av_skew_ms=150)
    assembler = WindowAssembler(
        session_id="session",
        duration_ns=1_000_000_000,
        health_monitor=health,
    )
    audio = AudioChunk(stamp(0, 100_000_000), 48_000, 2, "s16", bytes(400))
    video = VideoFrame(stamp(1, 120_000_000), 1, 1, "bgr24", b"\x10\x10\x10")
    next_video = VideoFrame(
        stamp(2, 2_100_000_000),
        1,
        1,
        "bgr24",
        b"\x20\x20\x20",
    )

    assert assembler.push(audio) == ()
    assert assembler.push(video) == ()
    emitted = assembler.push(next_video)

    assert len(emitted) == 2
    assert emitted[0].start_ns == 0
    assert emitted[0].audio == (audio,)
    assert emitted[0].video == (video,)
    assert emitted[1].start_ns == 1_000_000_000
    assert not emitted[1].audio
    assert not emitted[1].video


def test_flush_is_causal_and_does_not_emit_partial_window() -> None:
    assembler = WindowAssembler(
        session_id="session",
        duration_ns=1_000_000_000,
        health_monitor=CaptureHealthMonitor(stale_after_ms=1500, max_av_skew_ms=150),
    )
    assembler.push(AudioChunk(stamp(0, 100), 48_000, 2, "s16", bytes(400)))
    assert assembler.flush(999_999_999) == ()
    assert len(assembler.flush(1_000_000_000)) == 1


def test_marked_queue_gap_invalidates_the_next_completed_window() -> None:
    assembler = WindowAssembler(
        session_id="session",
        duration_ns=1_000_000_000,
        health_monitor=CaptureHealthMonitor(stale_after_ms=1500, max_av_skew_ms=150),
    )
    assembler.push(AudioChunk(stamp(0, 100), 48_000, 2, "s16", bytes(400)))
    assembler.mark_gap("queue overflow")

    window = assembler.push(VideoFrame(stamp(1, 1_000_000_000), 1, 1, "bgr24", b"\x10\x10\x10"))[0]

    assert "queue overflow" in window.health.reasons
    assert not window.health.valid
