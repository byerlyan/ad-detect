"""Shared deterministic PCM decoding helpers."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from ad_detect.domain import AudioChunk

FloatArray = npt.NDArray[np.float32]


def mono_audio(chunk: AudioChunk) -> FloatArray:
    dtypes: dict[str, tuple[npt.DTypeLike, float, float]] = {
        "u8": (np.uint8, 128.0, 128.0),
        "u8p": (np.uint8, 128.0, 128.0),
        "s16": (np.dtype("<i2"), 0.0, 32768.0),
        "s16p": (np.dtype("<i2"), 0.0, 32768.0),
        "s32": (np.dtype("<i4"), 0.0, 2_147_483_648.0),
        "s32p": (np.dtype("<i4"), 0.0, 2_147_483_648.0),
        "flt": (np.dtype("<f4"), 0.0, 1.0),
        "fltp": (np.dtype("<f4"), 0.0, 1.0),
        "dbl": (np.dtype("<f8"), 0.0, 1.0),
        "dblp": (np.dtype("<f8"), 0.0, 1.0),
    }
    encoding = dtypes.get(chunk.sample_format)
    if encoding is None or chunk.channels < 1:
        raise ValueError(f"unsupported audio format {chunk.sample_format!r}")
    dtype, offset, scale = encoding
    samples = np.frombuffer(chunk.pcm, dtype=dtype)
    usable = samples.size - samples.size % chunk.channels
    if usable == 0:
        raise ValueError("audio chunk contains no complete samples")
    samples = samples[:usable].astype(np.float32)
    if chunk.sample_format.endswith("p"):
        channel_samples = samples.reshape(chunk.channels, -1)
        mono = channel_samples.mean(axis=0)
    else:
        mono = samples.reshape(-1, chunk.channels).mean(axis=1)
    return np.asarray((mono - offset) / scale, dtype=np.float32)


def resample_linear(samples: FloatArray, source_rate: int, target_rate: int) -> FloatArray:
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate == target_rate:
        return samples.copy()
    target_count = max(1, round(samples.size * target_rate / source_rate))
    source_positions = np.arange(samples.size, dtype=np.float64)
    target_positions = np.arange(target_count, dtype=np.float64) * source_rate / target_rate
    target_positions = np.minimum(target_positions, max(0, samples.size - 1))
    return np.asarray(np.interp(target_positions, source_positions, samples), dtype=np.float32)
