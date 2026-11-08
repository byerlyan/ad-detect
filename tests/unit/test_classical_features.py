import numpy as np

from ad_detect.config.settings import FeatureSettings
from ad_detect.domain import (
    AudioChunk,
    CaptureHealth,
    MediaStamp,
    SynchronizedWindow,
    VideoFrame,
)
from ad_detect.features import ClassicalFeatureExtractor


def audio_chunk(samples: np.ndarray, *, sequence: int = 0) -> AudioChunk:
    stereo = np.repeat(samples[:, None], 2, axis=1).astype("<i2")
    stamp = MediaStamp("session", sequence, sequence * 1_000_000_000, sequence * 1_000_000_000)
    return AudioChunk(stamp, 16_000, 2, "s16", stereo.tobytes())


def video_frame(pixels: np.ndarray, *, sequence: int = 0) -> VideoFrame:
    height, width, _ = pixels.shape
    stamp = MediaStamp("session", sequence, sequence * 1_000_000_000, sequence * 1_000_000_000)
    return VideoFrame(stamp, width, height, "bgr24", pixels.astype(np.uint8).tobytes())


def healthy(at_ns: int = 1_000_000_000) -> CaptureHealth:
    return CaptureHealth(
        at_ns=at_ns,
        video_valid=True,
        audio_valid=True,
        stale_ms=0,
        av_skew_ms=0,
    )


def test_extracts_versioned_audio_video_and_health_features() -> None:
    time = np.arange(16_000) / 16_000
    tone = (np.sin(2 * np.pi * 440 * time) * 12_000).astype(np.int16)
    checker = np.indices((32, 32)).sum(axis=0) % 2
    image = np.repeat((checker * 255)[..., None], 3, axis=2)
    window = SynchronizedWindow(
        session_id="session",
        start_ns=0,
        end_ns=1_000_000_000,
        audio=(audio_chunk(tone),),
        video=(video_frame(image),),
        health=healthy(),
    )

    features = ClassicalFeatureExtractor(FeatureSettings()).extract(window)

    assert features.schema_version == "classical-v1"
    assert not features.missing
    assert 0.2 < features.values["audio.rms"] < 0.3
    assert features.values["audio.silence_ratio"] < 0.1
    assert features.values["video.edge_density.mean"] > 0.9
    assert features.values["health.stale_ms"] == 0.0


def test_spectral_flux_and_scene_cut_use_previous_window() -> None:
    extractor = ClassicalFeatureExtractor(FeatureSettings(scene_cut_threshold=0.1))
    quiet = np.zeros(16_000, dtype=np.int16)
    noise = np.random.default_rng(7).integers(-20_000, 20_000, 16_000, dtype=np.int16)
    dark = np.zeros((24, 24, 3), dtype=np.uint8)
    bright = np.full((24, 24, 3), 255, dtype=np.uint8)

    first = extractor.extract(
        SynchronizedWindow("session", 0, 1, (audio_chunk(quiet),), (video_frame(dark),), healthy())
    )
    second = extractor.extract(
        SynchronizedWindow(
            "session",
            1,
            2,
            (audio_chunk(noise, sequence=1),),
            (video_frame(bright, sequence=1),),
            healthy(),
        )
    )

    assert first.values["audio.spectral_flux"] == 0.0
    assert second.values["audio.spectral_flux"] > 0.2
    assert second.values["video.scene_cut.max"] > 0.9
    assert second.values["video.scene_cut_flag.mean"] == 1.0


def test_invalid_capture_marks_features_missing_without_inventing_values() -> None:
    health = CaptureHealth(
        at_ns=1,
        video_valid=False,
        audio_valid=False,
        stale_ms=2_000,
        reasons=("capture unavailable",),
    )
    features = ClassicalFeatureExtractor(FeatureSettings()).extract(
        SynchronizedWindow("session", 0, 1, (), (), health)
    )
    assert features.missing == frozenset({"audio", "video", "capture"})
    assert not any(key.startswith("audio.") for key in features.values)
    assert not any(key.startswith("video.") for key in features.values)
