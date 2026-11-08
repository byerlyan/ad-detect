"""Capture-health state independent from advertisement evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ad_detect.domain import AudioChunk, CaptureHealth, VideoFrame


@dataclass(frozen=True, slots=True)
class VideoHealthSample:
    at_ns: int
    valid: bool
    frame_digest: str
    mean_luma: float
    luma_variance: float


@dataclass(frozen=True, slots=True)
class AudioHealthSample:
    at_ns: int
    valid: bool
    sample_count: int


class CaptureHealthMonitor:
    def __init__(
        self,
        *,
        stale_after_ms: float,
        max_av_skew_ms: float,
        black_luma_threshold: float = 2.0,
        black_variance_threshold: float = 2.0,
        confirmation_samples: int = 3,
    ) -> None:
        if stale_after_ms <= 0 or max_av_skew_ms <= 0 or confirmation_samples < 1:
            raise ValueError("health-monitor limits must be positive")
        self._stale_after_ms = stale_after_ms
        self._max_av_skew_ms = max_av_skew_ms
        self._black_luma_threshold = black_luma_threshold
        self._black_variance_threshold = black_variance_threshold
        self._confirmation_samples = confirmation_samples
        self._video: VideoHealthSample | None = None
        self._audio: AudioHealthSample | None = None
        self._last_digest: str | None = None
        self._frozen_count = 0
        self._black_count = 0

    def observe_video(self, sample: VideoHealthSample) -> None:
        if sample.valid and sample.frame_digest and sample.frame_digest == self._last_digest:
            self._frozen_count += 1
        else:
            self._frozen_count = 1 if sample.valid else 0
        self._last_digest = sample.frame_digest if sample.valid else None
        is_black = (
            sample.valid
            and sample.mean_luma <= self._black_luma_threshold
            and sample.luma_variance <= self._black_variance_threshold
        )
        self._black_count = self._black_count + 1 if is_black else 0
        self._video = sample

    def observe_audio(self, sample: AudioHealthSample) -> None:
        self._audio = sample

    def snapshot(self, now_ns: int) -> CaptureHealth:
        video_valid = self._video is not None and self._video.valid
        audio_valid = self._audio is not None and self._audio.valid and self._audio.sample_count > 0
        latest_ns = max(
            self._video.at_ns if self._video is not None else 0,
            self._audio.at_ns if self._audio is not None else 0,
        )
        stale_ms = max(0.0, (now_ns - latest_ns) / 1_000_000)
        skew_ms = (
            abs(self._video.at_ns - self._audio.at_ns) / 1_000_000
            if self._video is not None and self._audio is not None
            else None
        )
        black = self._black_count >= self._confirmation_samples
        frozen = self._frozen_count >= self._confirmation_samples
        reasons: list[str] = []
        if not video_valid:
            reasons.append("video unavailable")
        if not audio_valid:
            reasons.append("audio unavailable")
        if stale_ms > self._stale_after_ms:
            reasons.append("capture stale")
        if skew_ms is not None and skew_ms > self._max_av_skew_ms:
            reasons.append("A/V skew exceeds limit")
        if black:
            reasons.append("sustained black video")
        if frozen:
            reasons.append("sustained frozen video")
        return CaptureHealth(
            at_ns=now_ns,
            video_valid=video_valid,
            audio_valid=audio_valid,
            stale_ms=stale_ms,
            av_skew_ms=skew_ms,
            black_frame=black,
            frozen_frame=frozen,
            reasons=tuple(reasons),
        )


def video_health_sample(frame: VideoFrame, *, stride_pixels: int = 64) -> VideoHealthSample:
    """Compute inexpensive sampled luma statistics without importing OpenCV."""
    if stride_pixels < 1:
        raise ValueError("stride_pixels must be positive")
    expected = frame.width * frame.height * 3
    valid = frame.pixel_format == "bgr24" and len(frame.pixels) == expected
    if not valid:
        return VideoHealthSample(
            at_ns=frame.stamp.captured_monotonic_ns,
            valid=False,
            frame_digest="",
            mean_luma=0.0,
            luma_variance=0.0,
        )
    sample_step = stride_pixels * 3
    count = 0
    total = 0.0
    total_squared = 0.0
    for offset in range(0, len(frame.pixels) - 2, sample_step):
        blue, green, red = frame.pixels[offset : offset + 3]
        luma = (blue + 2 * green + red) / 4.0
        count += 1
        total += luma
        total_squared += luma * luma
    mean = total / count if count else 0.0
    variance = max(0.0, total_squared / count - mean * mean) if count else 0.0
    digest_stride = max(1, len(frame.pixels) // 4096)
    digest = hashlib.blake2b(frame.pixels[::digest_stride], digest_size=8).hexdigest()
    return VideoHealthSample(
        at_ns=frame.stamp.captured_monotonic_ns,
        valid=count > 0,
        frame_digest=digest,
        mean_luma=mean,
        luma_variance=variance,
    )


def audio_health_sample(chunk: AudioChunk) -> AudioHealthSample:
    widths = {
        "u8": 1,
        "u8p": 1,
        "s16": 2,
        "s16p": 2,
        "s32": 4,
        "s32p": 4,
        "flt": 4,
        "fltp": 4,
        "dbl": 8,
        "dblp": 8,
    }
    width = widths.get(chunk.sample_format)
    valid = width is not None and chunk.channels > 0 and chunk.sample_rate_hz > 0
    count = len(chunk.pcm) // (width * chunk.channels) if valid and width is not None else 0
    return AudioHealthSample(
        at_ns=chunk.stamp.captured_monotonic_ns,
        valid=valid and count > 0,
        sample_count=count,
    )


class MediaHealthConsumer:
    """Translate decoded records into the capture-health monitor."""

    def __init__(self, monitor: CaptureHealthMonitor) -> None:
        self.monitor = monitor

    def observe(self, record: AudioChunk | VideoFrame) -> None:
        if isinstance(record, VideoFrame):
            self.monitor.observe_video(video_health_sample(record))
        else:
            self.monitor.observe_audio(audio_health_sample(record))
