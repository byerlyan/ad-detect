from ad_detect.capture import (
    AudioHealthSample,
    CaptureHealthMonitor,
    VideoHealthSample,
    audio_health_sample,
    video_health_sample,
)
from ad_detect.domain import AudioChunk, MediaStamp, VideoFrame


def monitor() -> CaptureHealthMonitor:
    return CaptureHealthMonitor(
        stale_after_ms=1500,
        max_av_skew_ms=150,
        confirmation_samples=3,
    )


def test_healthy_synchronized_capture() -> None:
    health = monitor()
    health.observe_video(VideoHealthSample(1_000_000_000, True, "a", 80.0, 10.0))
    health.observe_audio(AudioHealthSample(1_030_000_000, True, 48_000))
    result = health.snapshot(1_040_000_000)
    assert result.valid
    assert result.av_skew_ms == 30.0


def test_sustained_black_and_frozen_video_are_unhealthy() -> None:
    health = monitor()
    for index in range(3):
        health.observe_video(VideoHealthSample(1_000_000_000 + index, True, "same", 0.0, 0.0))
    health.observe_audio(AudioHealthSample(1_000_000_002, True, 48_000))
    result = health.snapshot(1_000_000_003)
    assert not result.valid
    assert result.black_frame
    assert result.frozen_frame
    assert "sustained black video" in result.reasons


def test_missing_audio_and_stale_capture_are_unhealthy() -> None:
    health = monitor()
    health.observe_video(VideoHealthSample(1_000_000_000, True, "frame", 80.0, 10.0))
    result = health.snapshot(3_000_000_000)
    assert not result.valid
    assert "audio unavailable" in result.reasons
    assert "capture stale" in result.reasons


def test_decoded_media_converts_to_health_samples() -> None:
    stamp = MediaStamp("s", 1, 0, 1_000_000_000)
    frame = VideoFrame(stamp, 2, 2, "bgr24", bytes([100, 100, 100] * 4))
    audio = AudioChunk(stamp, 48_000, 2, "s16", bytes(48_000 * 2 * 2))

    video_sample = video_health_sample(frame, stride_pixels=1)
    audio_sample = audio_health_sample(audio)

    assert video_sample.valid
    assert video_sample.mean_luma == 100.0
    assert video_sample.luma_variance == 0.0
    assert audio_sample.valid
    assert audio_sample.sample_count == 48_000


def test_invalid_decoded_shapes_fail_health_closed() -> None:
    stamp = MediaStamp("s", 1, 0, 1_000_000_000)
    frame = VideoFrame(stamp, 1920, 1080, "bgr24", b"too short")
    audio = AudioChunk(stamp, 48_000, 2, "unknown", b"audio")
    assert not video_health_sample(frame).valid
    assert not audio_health_sample(audio).valid
