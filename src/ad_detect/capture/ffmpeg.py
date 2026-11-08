"""FFmpeg device discovery, probing, and single-owner capture process."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ad_detect.config.settings import CaptureSettings
from ad_detect.domain import CaptureDevice, CaptureDeviceKind, CaptureProbe

_DEVICE_LINE = re.compile(r"\]\s+\[(?P<index>\d+)\]\s+(?P<name>.+)$")
_VIDEO_STREAM = re.compile(r"Stream #\S+.*Video:\s*(?P<format>.+)$")
_AUDIO_STREAM = re.compile(r"Stream #\S+.*Audio:\s*(?P<format>.+)$")


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class CommandRunner(Protocol):
    async def run(self, command: Sequence[str], *, timeout: float) -> CommandResult: ...


class AsyncCommandRunner:
    async def run(self, command: Sequence[str], *, timeout: float) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return CommandResult(process.returncode or 0, stdout, stderr)


def parse_avfoundation_devices(output: str) -> tuple[CaptureDevice, ...]:
    """Parse FFmpeg's stderr device listing without depending on pointer prefixes."""
    kind: CaptureDeviceKind | None = None
    devices: list[CaptureDevice] = []
    for raw_line in output.splitlines():
        if "AVFoundation video devices:" in raw_line:
            kind = CaptureDeviceKind.VIDEO
            continue
        if "AVFoundation audio devices:" in raw_line:
            kind = CaptureDeviceKind.AUDIO
            continue
        match = _DEVICE_LINE.search(raw_line)
        if kind is not None and match:
            devices.append(
                CaptureDevice(
                    kind=kind,
                    index=int(match.group("index")),
                    name=match.group("name").strip(),
                )
            )
    return tuple(devices)


def resolve_device(
    devices: Sequence[CaptureDevice],
    *,
    kind: CaptureDeviceKind,
    selector: str,
) -> CaptureDevice:
    """Resolve an exact name or current index and reject ambiguous names."""
    candidates = [device for device in devices if device.kind is kind]
    if not selector:
        raise ValueError(f"no {kind.value} capture device selected")
    if selector.isdecimal():
        index = int(selector)
        matches = [device for device in candidates if device.index == index]
    else:
        matches = [device for device in candidates if device.name == selector]
    if not matches:
        available = ", ".join(f"{item.index}:{item.name}" for item in candidates) or "none"
        raise ValueError(f"{kind.value} device {selector!r} not found; available: {available}")
    if len(matches) > 1:
        raise ValueError(f"{kind.value} device name {selector!r} is ambiguous")
    return matches[0]


class FFmpegAVFoundation:
    def __init__(
        self,
        settings: CaptureSettings,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.settings = settings
        self._runner = runner or AsyncCommandRunner()

    async def version(self) -> str:
        result = await self._runner.run(
            (self.settings.ffmpeg_path, "-version"),
            timeout=5.0,
        )
        if result.returncode != 0:
            raise RuntimeError("FFmpeg version probe failed")
        first_line = result.stdout.decode(errors="replace").splitlines()
        if not first_line:
            raise RuntimeError("FFmpeg version probe returned no output")
        return first_line[0]

    async def discover(self) -> tuple[CaptureDevice, ...]:
        command = (
            self.settings.ffmpeg_path,
            "-hide_banner",
            "-f",
            "avfoundation",
            "-list_devices",
            "true",
            "-i",
            "",
        )
        result = await self._runner.run(command, timeout=10.0)
        # FFmpeg normally exits non-zero after listing because no output is set.
        return parse_avfoundation_devices(result.stderr.decode(errors="replace"))

    async def selected_devices(self) -> tuple[CaptureDevice, CaptureDevice]:
        devices = await self.discover()
        video = resolve_device(
            devices,
            kind=CaptureDeviceKind.VIDEO,
            selector=self.settings.video_device,
        )
        audio = resolve_device(
            devices,
            kind=CaptureDeviceKind.AUDIO,
            selector=self.settings.audio_device,
        )
        return video, audio

    def input_arguments(self, video: CaptureDevice, audio: CaptureDevice) -> tuple[str, ...]:
        return (
            "-thread_queue_size",
            "512",
            "-f",
            "avfoundation",
            "-framerate",
            f"{self.settings.frame_rate:g}",
            "-video_size",
            f"{self.settings.width}x{self.settings.height}",
            "-video_device_index",
            str(video.index),
            "-audio_device_index",
            str(audio.index),
            "-i",
            f"{video.index}:{audio.index}",
        )

    async def probe(self) -> CaptureProbe:
        try:
            video, audio = await self.selected_devices()
        except ValueError as exc:
            return CaptureProbe(
                success=False,
                command=(),
                duration_seconds=self.settings.probe_seconds,
                negotiated_video=None,
                negotiated_audio=None,
                stderr_tail=(),
                reason=str(exc),
            )
        command = (
            self.settings.ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            *self.input_arguments(video, audio),
            "-t",
            f"{self.settings.probe_seconds:g}",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        )
        timeout = self.settings.probe_seconds + 10.0
        try:
            result = await self._runner.run(command, timeout=timeout)
        except TimeoutError:
            return CaptureProbe(
                success=False,
                command=command,
                duration_seconds=self.settings.probe_seconds,
                negotiated_video=None,
                negotiated_audio=None,
                stderr_tail=(),
                reason="FFmpeg capture probe timed out",
            )
        stderr = result.stderr.decode(errors="replace")
        lines = tuple(stderr.splitlines())
        video_match = next((_VIDEO_STREAM.search(line) for line in lines if "Video:" in line), None)
        audio_match = next((_AUDIO_STREAM.search(line) for line in lines if "Audio:" in line), None)
        if result.returncode != 0:
            reason = f"FFmpeg exited with status {result.returncode}"
        elif video_match is None or audio_match is None:
            reason = "FFmpeg probe did not expose both video and audio streams"
        else:
            reason = None
        return CaptureProbe(
            success=result.returncode == 0 and video_match is not None and audio_match is not None,
            command=command,
            duration_seconds=self.settings.probe_seconds,
            negotiated_video=video_match.group("format") if video_match else None,
            negotiated_audio=audio_match.group("format") if audio_match else None,
            stderr_tail=lines[-20:],
            reason=reason,
        )

    def capture_command(
        self,
        video: CaptureDevice,
        audio: CaptureDevice,
    ) -> tuple[str, ...]:
        """Build a timestamp-preserving NUT stream for a dedicated decoder worker."""
        return (
            self.settings.ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-fflags",
            "+genpts+discardcorrupt",
            *self.input_arguments(video, audio),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(self.settings.sample_rate_hz),
            "-ac",
            str(self.settings.channels),
            "-f",
            "nut",
            "pipe:1",
        )
