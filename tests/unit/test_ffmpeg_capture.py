from collections.abc import Sequence

import pytest

from ad_detect.capture import (
    CommandResult,
    FFmpegAVFoundation,
    parse_avfoundation_devices,
    resolve_device,
)
from ad_detect.config.settings import CaptureSettings
from ad_detect.domain import CaptureDeviceKind

DEVICE_LIST = """
[AVFoundation indev @ 0x123] AVFoundation video devices:
[AVFoundation indev @ 0x123] [0] FaceTime HD Camera
[AVFoundation indev @ 0x123] [1] MS2130 Video
[AVFoundation indev @ 0x123] AVFoundation audio devices:
[AVFoundation indev @ 0x123] [0] MacBook Microphone
[AVFoundation indev @ 0x123] [1] MS2130 Audio
"""


class FakeRunner:
    def __init__(self, *results: CommandResult) -> None:
        self.results = list(results)
        self.commands: list[tuple[str, ...]] = []

    async def run(self, command: Sequence[str], *, timeout: float) -> CommandResult:
        self.commands.append(tuple(command))
        assert timeout > 0
        return self.results.pop(0)


def capture_settings() -> CaptureSettings:
    return CaptureSettings(video_device="MS2130 Video", audio_device="MS2130 Audio")


def test_parse_and_resolve_avfoundation_devices() -> None:
    devices = parse_avfoundation_devices(DEVICE_LIST)
    assert [(device.kind.value, device.index, device.name) for device in devices] == [
        ("video", 0, "FaceTime HD Camera"),
        ("video", 1, "MS2130 Video"),
        ("audio", 0, "MacBook Microphone"),
        ("audio", 1, "MS2130 Audio"),
    ]
    assert resolve_device(devices, kind=CaptureDeviceKind.VIDEO, selector="MS2130 Video").index == 1
    assert (
        resolve_device(devices, kind=CaptureDeviceKind.AUDIO, selector="1").name == "MS2130 Audio"
    )


def test_resolve_reports_available_devices() -> None:
    devices = parse_avfoundation_devices(DEVICE_LIST)
    with pytest.raises(ValueError, match="available: 0:FaceTime HD Camera, 1:MS2130 Video"):
        resolve_device(devices, kind=CaptureDeviceKind.VIDEO, selector="missing")


def test_capture_command_uses_one_timestamped_audio_video_input() -> None:
    devices = parse_avfoundation_devices(DEVICE_LIST)
    video = resolve_device(devices, kind=CaptureDeviceKind.VIDEO, selector="MS2130 Video")
    audio = resolve_device(devices, kind=CaptureDeviceKind.AUDIO, selector="MS2130 Audio")
    command = FFmpegAVFoundation(capture_settings()).capture_command(video, audio)
    assert command.count("-i") == 1
    assert "1:1" in command
    assert (command[-3], command[-2]) == ("-f", "nut")
    assert command[-1] == "pipe:1"


@pytest.mark.asyncio
async def test_probe_requires_negotiated_audio_and_video() -> None:
    probe_output = b"""
Stream #0:0: Video: rawvideo, uyvy422, 1280x720, 30 fps
Stream #0:1: Audio: pcm_s16le, 48000 Hz, stereo, s16
"""
    runner = FakeRunner(
        CommandResult(1, b"", DEVICE_LIST.encode()),
        CommandResult(0, b"", probe_output),
    )
    adapter = FFmpegAVFoundation(capture_settings(), runner=runner)

    result = await adapter.probe()

    assert result.success
    assert result.negotiated_video and "1280x720" in result.negotiated_video
    assert result.negotiated_audio and "48000 Hz" in result.negotiated_audio
    assert runner.commands[1][-2:] == ("null", "-")


@pytest.mark.asyncio
async def test_probe_fails_closed_when_audio_is_missing() -> None:
    runner = FakeRunner(
        CommandResult(1, b"", DEVICE_LIST.encode()),
        CommandResult(0, b"", b"Stream #0:0: Video: rawvideo, 1280x720\n"),
    )
    result = await FFmpegAVFoundation(capture_settings(), runner=runner).probe()
    assert not result.success
    assert result.reason == "FFmpeg probe did not expose both video and audio streams"


@pytest.mark.asyncio
async def test_unselected_devices_return_actionable_probe_failure() -> None:
    runner = FakeRunner(CommandResult(1, b"", DEVICE_LIST.encode()))
    result = await FFmpegAVFoundation(CaptureSettings(), runner=runner).probe()
    assert not result.success
    assert result.reason == "no video capture device selected"
