import pytest

from ad_detect.data import RollingMediaBuffer
from ad_detect.domain import AudioChunk, MediaStamp


def record(sequence: int, at_ns: int, size: int = 10) -> AudioChunk:
    return AudioChunk(MediaStamp("s", sequence, at_ns, at_ns), 48_000, 1, "u8", bytes(size))


def test_buffer_evicts_by_duration_and_returns_spans() -> None:
    buffer = RollingMediaBuffer(duration_ns=100, max_bytes=1_000)
    buffer.append(record(0, 0))
    buffer.append(record(1, 50))
    buffer.append(record(2, 150))
    assert [item.stamp.sequence for item in buffer.span(0, 200)] == [1, 2]
    assert buffer.stats().evicted_records == 1


def test_buffer_evicts_by_bytes() -> None:
    buffer = RollingMediaBuffer(duration_ns=1_000, max_bytes=15)
    buffer.append(record(0, 0, 10))
    buffer.append(record(1, 1, 10))
    assert buffer.stats().records == 1
    assert buffer.stats().bytes_used == 10


def test_buffer_rejects_out_of_order_records_and_invalid_spans() -> None:
    buffer = RollingMediaBuffer(duration_ns=100, max_bytes=100)
    buffer.append(record(0, 10))
    with pytest.raises(ValueError, match="time ordered"):
        buffer.append(record(1, 9))
    with pytest.raises(ValueError, match="span end"):
        buffer.span(5, 5)
