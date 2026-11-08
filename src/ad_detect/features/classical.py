"""Low-cost, versioned features for the rules and tabular ML baselines."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
import numpy.typing as npt

from ad_detect.config.settings import FeatureSettings
from ad_detect.domain import (
    AudioChunk,
    FeatureWindow,
    SynchronizedWindow,
    VideoFrame,
    feature_values,
)

from .audio import mono_audio

FloatArray = npt.NDArray[np.float32]


def _aggregate(
    feature_maps: Iterable[Mapping[str, float]],
    *,
    prefix: str,
) -> dict[str, float]:
    rows = tuple(feature_maps)
    if not rows:
        return {}
    result: dict[str, float] = {}
    for key in rows[0]:
        values = np.asarray([row[key] for row in rows], dtype=np.float64)
        result[f"{prefix}.{key}.mean"] = float(values.mean())
        result[f"{prefix}.{key}.max"] = float(values.max())
        result[f"{prefix}.{key}.std"] = float(values.std())
    return result


class ClassicalFeatureExtractor:
    def __init__(self, settings: FeatureSettings) -> None:
        self.settings = settings
        self._previous_audio_spectrum: FloatArray | None = None
        self._previous_video_histogram: FloatArray | None = None

    def reset(self) -> None:
        """Clear temporal feature history after a capture discontinuity."""
        self._previous_audio_spectrum = None
        self._previous_video_histogram = None

    def _audio_features(self, chunks: tuple[AudioChunk, ...]) -> dict[str, float]:
        mono_chunks = [mono_audio(chunk) for chunk in chunks]
        mono = np.concatenate(mono_chunks)
        absolute = np.abs(mono)
        rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
        peak = float(absolute.max(initial=0.0))
        zero_crossing = float(np.mean(np.signbit(mono[1:]) != np.signbit(mono[:-1])))
        silence_ratio = float(np.mean(absolute <= self.settings.silence_amplitude))
        crest = peak / max(rms, 1e-9)

        window = np.hanning(mono.size).astype(np.float32)
        spectrum = np.abs(np.fft.rfft(mono * window)).astype(np.float32)
        spectrum_sum = float(spectrum.sum())
        normalized = spectrum / max(spectrum_sum, 1e-12)
        frequencies = np.fft.rfftfreq(mono.size, d=1.0 / chunks[0].sample_rate_hz)
        centroid = float(np.sum(frequencies * normalized))
        geometric = float(np.exp(np.mean(np.log(spectrum + 1e-12))))
        flatness = geometric / max(float(spectrum.mean()), 1e-12)
        if (
            self._previous_audio_spectrum is None
            or self._previous_audio_spectrum.shape != normalized.shape
        ):
            spectral_flux = 0.0
        else:
            spectral_flux = float(0.5 * np.abs(normalized - self._previous_audio_spectrum).sum())
        self._previous_audio_spectrum = normalized

        nyquist = chunks[0].sample_rate_hz / 2
        low = float(normalized[frequencies < min(300.0, nyquist)].sum())
        mid = float(normalized[(frequencies >= 300.0) & (frequencies < min(3000.0, nyquist))].sum())
        high = max(0.0, 1.0 - low - mid)
        return {
            "rms": rms,
            "peak": peak,
            "crest_factor": crest,
            "zero_crossing_rate": zero_crossing,
            "silence_ratio": silence_ratio,
            "spectral_centroid_hz": centroid,
            "spectral_flatness": flatness,
            "spectral_flux": spectral_flux,
            "low_band_ratio": low,
            "mid_band_ratio": mid,
            "high_band_ratio": high,
        }

    def _video_features(self, frame: VideoFrame) -> dict[str, float]:
        if frame.pixel_format != "bgr24":
            raise ValueError(f"unsupported video format {frame.pixel_format!r}")
        expected = frame.width * frame.height * 3
        if len(frame.pixels) != expected:
            raise ValueError("video frame byte length does not match dimensions")
        image = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(frame.height, frame.width, 3)
        step = max(1, min(frame.width, frame.height) // 180)
        sampled = image[::step, ::step]
        blue = sampled[..., 0].astype(np.float32)
        green = sampled[..., 1].astype(np.float32)
        red = sampled[..., 2].astype(np.float32)
        luma = 0.114 * blue + 0.587 * green + 0.299 * red
        horizontal = np.abs(np.diff(luma, axis=1))
        vertical = np.abs(np.diff(luma, axis=0))
        edge_density = float((np.mean(horizontal > 20.0) + np.mean(vertical > 20.0)) / 2.0)
        histogram, _ = np.histogram(luma, bins=32, range=(0.0, 256.0))
        normalized_histogram = histogram.astype(np.float32)
        normalized_histogram /= max(float(normalized_histogram.sum()), 1.0)
        if self._previous_video_histogram is None:
            scene_cut = 0.0
        else:
            scene_cut = float(
                0.5 * np.abs(normalized_histogram - self._previous_video_histogram).sum()
            )
        self._previous_video_histogram = normalized_histogram
        colorfulness = float(
            np.mean(sampled.max(axis=2).astype(np.float32) - sampled.min(axis=2)) / 255.0
        )
        return {
            "luma_mean": float(luma.mean()),
            "luma_std": float(luma.std()),
            "black_ratio": float(np.mean(luma < 8.0)),
            "edge_density": edge_density,
            "colorfulness": colorfulness,
            "scene_cut": scene_cut,
            "scene_cut_flag": float(scene_cut >= self.settings.scene_cut_threshold),
        }

    def extract(self, window: SynchronizedWindow) -> FeatureWindow:
        values: dict[str, float] = {
            "health.stale_ms": window.health.stale_ms,
            "health.av_skew_ms": window.health.av_skew_ms or 0.0,
            "health.black": float(window.health.black_frame),
            "health.frozen": float(window.health.frozen_frame),
        }
        missing: set[str] = set()
        if window.audio and window.health.audio_valid:
            try:
                values.update(
                    {
                        f"audio.{key}": value
                        for key, value in self._audio_features(window.audio).items()
                    }
                )
            except ValueError:
                missing.add("audio")
        else:
            missing.add("audio")
        if window.video and window.health.video_valid:
            maps: list[dict[str, float]] = []
            for frame in window.video:
                try:
                    maps.append(self._video_features(frame))
                except ValueError:
                    missing.add("video")
            values.update(_aggregate(maps, prefix="video"))
        else:
            missing.add("video")
        if not window.health.valid:
            missing.add("capture")
        return FeatureWindow(
            schema_version=self.settings.schema_version,
            session_id=window.session_id,
            start_ns=window.start_ns,
            end_ns=window.end_ns,
            values=feature_values(values),
            missing=frozenset(missing),
        )
