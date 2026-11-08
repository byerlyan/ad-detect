"""Dependency-neutral benchmark boundary for optional embedding experiments."""

from __future__ import annotations

import resource
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Protocol

import numpy as np


class EmbeddingAdapter(Protocol):
    name: str
    version: str

    def load(self) -> None: ...

    def embed(self, sample: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class EmbeddingBenchmark:
    adapter: str
    version: str
    samples: int
    dimensions: int
    load_ms: float
    inference_mean_ms: float
    inference_p95_ms: float
    inference_max_ms: float
    peak_rss_bytes: int


def benchmark_embedding_adapter(
    adapter: EmbeddingAdapter,
    samples: Sequence[np.ndarray],
) -> EmbeddingBenchmark:
    if not samples:
        raise ValueError("embedding benchmark requires samples")
    load_started = time.perf_counter()
    adapter.load()
    load_ms = (time.perf_counter() - load_started) * 1000
    latencies: list[float] = []
    dimensions: int | None = None
    for sample in samples:
        started = time.perf_counter()
        embedding = np.asarray(adapter.embed(sample), dtype=np.float32)
        latencies.append((time.perf_counter() - started) * 1000)
        if embedding.ndim != 1 or not np.all(np.isfinite(embedding)):
            raise ValueError("embedding adapter must return one finite vector")
        if dimensions is None:
            dimensions = embedding.size
        elif embedding.size != dimensions:
            raise ValueError("embedding dimensions changed during benchmark")
    ordered = sorted(latencies)
    p95_index = min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return EmbeddingBenchmark(
        adapter=adapter.name,
        version=adapter.version,
        samples=len(samples),
        dimensions=dimensions or 0,
        load_ms=load_ms,
        inference_mean_ms=fmean(latencies),
        inference_p95_ms=ordered[p95_index],
        inference_max_ms=max(latencies),
        peak_rss_bytes=rss if sys.platform == "darwin" else rss * 1024,
    )
