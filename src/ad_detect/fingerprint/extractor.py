"""Deterministic Shazam-style time/frequency landmark extraction."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from ad_detect.config.settings import FingerprintSettings
from ad_detect.domain import AudioChunk, AudioLandmark
from ad_detect.features.audio import mono_audio, resample_linear

FloatArray = npt.NDArray[np.float32]


class LandmarkExtractor:
    def __init__(self, settings: FingerprintSettings) -> None:
        self.settings = settings

    def samples(self, chunks: Sequence[AudioChunk]) -> FloatArray:
        if not chunks:
            raise ValueError("fingerprinting requires at least one audio chunk")
        source_rate = chunks[0].sample_rate_hz
        if any(chunk.sample_rate_hz != source_rate for chunk in chunks):
            raise ValueError("fingerprint audio chunks must use one sample rate")
        samples = np.concatenate([mono_audio(chunk) for chunk in chunks])
        samples = resample_linear(samples, source_rate, self.settings.sample_rate_hz)
        samples -= float(samples.mean())
        peak = float(np.abs(samples).max(initial=0.0))
        if peak > 1e-9:
            samples /= peak
        return np.asarray(samples, dtype=np.float32)

    def extract(self, chunks: Sequence[AudioChunk]) -> tuple[AudioLandmark, ...]:
        return self.extract_samples(self.samples(chunks))

    def extract_samples(self, samples: FloatArray) -> tuple[AudioLandmark, ...]:
        if samples.size < self.settings.fft_size:
            return ()
        frames = np.lib.stride_tricks.sliding_window_view(samples, self.settings.fft_size)[
            :: self.settings.hop_size
        ]
        window = np.hanning(self.settings.fft_size).astype(np.float32)
        magnitude = np.log1p(np.abs(np.fft.rfft(frames * window, axis=1))).astype(np.float32)
        peaks = self._peaks(magnitude)
        landmarks: set[tuple[int, int]] = set()
        for anchor_frame, anchor_peaks in enumerate(peaks):
            last_target = min(len(peaks), anchor_frame + self.settings.target_max_frames + 1)
            for first_bin, _ in anchor_peaks:
                candidates: list[tuple[int, float, int]] = []
                for target_frame in range(
                    anchor_frame + self.settings.target_min_frames,
                    last_target,
                ):
                    delta = target_frame - anchor_frame
                    for second_bin, strength in peaks[target_frame]:
                        candidates.append((delta, -strength, second_bin))
                for delta, _, second_bin in sorted(candidates)[: self.settings.fanout]:
                    hash_value = (first_bin << 20) | (second_bin << 8) | delta
                    anchor_ms = round(
                        anchor_frame * self.settings.hop_size * 1000 / self.settings.sample_rate_hz
                    )
                    landmarks.add((hash_value, anchor_ms))
        return tuple(
            AudioLandmark(hash_value=hash_value, anchor_ms=anchor_ms)
            for hash_value, anchor_ms in sorted(landmarks, key=lambda item: (item[1], item[0]))
        )

    def _peaks(self, magnitude: FloatArray) -> list[list[tuple[int, float]]]:
        nyquist = self.settings.sample_rate_hz / 2
        bin_hz = self.settings.sample_rate_hz / self.settings.fft_size
        first_bin = max(1, int(80 / bin_hz))
        last_bin = min(magnitude.shape[1] - 1, int(min(7500.0, nyquist) / bin_hz))
        radius = self.settings.peak_time_radius
        padded = np.pad(
            magnitude,
            ((radius, radius), (0, 0)),
            mode="constant",
            constant_values=-np.inf,
        )
        temporal_max = np.lib.stride_tricks.sliding_window_view(padded, 2 * radius + 1, axis=0).max(
            axis=2
        )
        result: list[list[tuple[int, float]]] = []
        for frame_index, spectrum in enumerate(magnitude):
            usable = spectrum[first_bin:last_bin]
            threshold = float(np.percentile(usable, self.settings.peak_percentile))
            candidates: list[tuple[int, float]] = []
            for frequency_bin in range(first_bin, last_bin):
                strength = float(spectrum[frequency_bin])
                if (
                    strength > threshold
                    and strength >= float(spectrum[frequency_bin - 1])
                    and strength >= float(spectrum[frequency_bin + 1])
                    and strength >= float(temporal_max[frame_index, frequency_bin])
                ):
                    candidates.append((frequency_bin, strength))
            candidates.sort(key=lambda item: (-item[1], item[0]))
            result.append(candidates[: self.settings.peaks_per_frame])
        return result
