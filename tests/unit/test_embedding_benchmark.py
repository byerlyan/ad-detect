import numpy as np
import pytest

from ad_detect.learning import benchmark_embedding_adapter


class FakeEmbeddingAdapter:
    name = "fake"
    version = "1"

    def __init__(self, *, inconsistent: bool = False) -> None:
        self.loaded = False
        self.inconsistent = inconsistent
        self.calls = 0

    def load(self) -> None:
        self.loaded = True

    def embed(self, sample: np.ndarray) -> np.ndarray:
        assert self.loaded
        self.calls += 1
        size = 3 if not self.inconsistent or self.calls == 1 else 2
        return np.full(size, float(sample.mean()), dtype=np.float32)


def test_embedding_adapter_benchmark_records_shape_latency_and_memory() -> None:
    report = benchmark_embedding_adapter(
        FakeEmbeddingAdapter(),
        [np.ones((2, 2), dtype=np.float32), np.zeros((2, 2), dtype=np.float32)],
    )

    assert report.adapter == "fake"
    assert report.samples == 2
    assert report.dimensions == 3
    assert report.inference_max_ms >= 0
    assert report.peak_rss_bytes > 0


def test_embedding_benchmark_rejects_changing_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions changed"):
        benchmark_embedding_adapter(
            FakeEmbeddingAdapter(inconsistent=True),
            [np.ones(2, dtype=np.float32), np.ones(2, dtype=np.float32)],
        )
