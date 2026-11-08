import asyncio
from collections.abc import Callable, Sequence
from fractions import Fraction

import pytest

from ad_detect.capture import CaptureSupervisor, MediaRecord, TimestampNormalizer
from ad_detect.domain import CaptureDevice, CaptureDeviceKind, MediaStamp, VideoFrame


class FakeAdapter:
    async def selected_devices(self) -> tuple[CaptureDevice, CaptureDevice]:
        return (
            CaptureDevice(CaptureDeviceKind.VIDEO, 1, "video"),
            CaptureDevice(CaptureDeviceKind.AUDIO, 2, "audio"),
        )

    def capture_command(
        self,
        video: CaptureDevice,
        audio: CaptureDevice,
    ) -> tuple[str, ...]:
        return ("fake-ffmpeg", f"{video.index}:{audio.index}")


class FakeSession:
    def __init__(
        self,
        publish: Callable[[MediaRecord], None],
        *,
        should_fail: bool = False,
    ) -> None:
        self.publish = publish
        self.should_fail = should_fail
        self.stopped = False

    def run(self) -> None:
        if self.should_fail:
            raise RuntimeError("simulated capture crash")
        self.publish(
            VideoFrame(
                stamp=MediaStamp("session", 0, 0, 0),
                width=1,
                height=1,
                pixel_format="bgr24",
                pixels=b"\x00\x00\x00",
            )
        )

    def stop(self) -> None:
        self.stopped = True


def test_timestamp_normalizer_tracks_discontinuities_per_stream() -> None:
    normalizer = TimestampNormalizer("s", origin_ns=1_000_000_000)
    video = normalizer.stamp(100, Fraction(1, 1000), stream_key="video")
    audio = normalizer.stamp(90, Fraction(1, 1000), stream_key="audio")
    regressed_video = normalizer.stamp(99, Fraction(1, 1000), stream_key="video")

    assert video.captured_monotonic_ns == 1_000_000_000
    assert not audio.discontinuity
    assert regressed_video.discontinuity
    assert [video.sequence, audio.sequence, regressed_video.sequence] == [0, 1, 2]


@pytest.mark.asyncio
async def test_supervisor_publishes_decoded_media_to_bounded_consumers() -> None:
    def factory(
        command: Sequence[str],
        session_id: str,
        publish: Callable[[MediaRecord], None],
        report_error: Callable[[str], None],
    ) -> FakeSession:
        del command, session_id, report_error
        return FakeSession(publish)

    supervisor = CaptureSupervisor(
        FakeAdapter(),
        session_id="session",
        queue_capacity=2,
        max_restarts=0,
        session_factory=factory,
    )
    consumer = supervisor.subscribe("features")
    await supervisor.start()
    await supervisor.wait()
    await asyncio.sleep(0)

    record = consumer.receive_nowait()
    assert isinstance(record, VideoFrame)
    assert record.width == 1
    assert not supervisor.state().running


@pytest.mark.asyncio
async def test_supervisor_restarts_failed_session_with_bound() -> None:
    attempts = 0

    def factory(
        command: Sequence[str],
        session_id: str,
        publish: Callable[[MediaRecord], None],
        report_error: Callable[[str], None],
    ) -> FakeSession:
        nonlocal attempts
        del command, session_id, report_error
        attempts += 1
        return FakeSession(publish, should_fail=True)

    supervisor = CaptureSupervisor(
        FakeAdapter(),
        session_id="session",
        queue_capacity=1,
        max_restarts=1,
        restart_delay_seconds=0,
        session_factory=factory,
    )
    await supervisor.start()
    await supervisor.wait()

    assert attempts == 2
    assert supervisor.state().restart_count == 1
    assert supervisor.state().last_error == "simulated capture crash"
