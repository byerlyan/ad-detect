"""Capture adapters and bounded fan-out primitives."""

from .decoder import (
    CaptureSupervisor,
    CaptureSupervisorState,
    FFmpegDecodeSession,
    MediaRecord,
    TimestampNormalizer,
)
from .fanout import BoundedFanout, ChannelStats, DropOldestChannel
from .ffmpeg import (
    AsyncCommandRunner,
    CommandResult,
    FFmpegAVFoundation,
    parse_avfoundation_devices,
    resolve_device,
)
from .health import (
    AudioHealthSample,
    CaptureHealthMonitor,
    MediaHealthConsumer,
    VideoHealthSample,
    audio_health_sample,
    video_health_sample,
)
from .simulated import SimulatedCapture, SimulatedTick

__all__ = [
    "BoundedFanout",
    "CaptureSupervisor",
    "CaptureSupervisorState",
    "ChannelStats",
    "DropOldestChannel",
    "AsyncCommandRunner",
    "CommandResult",
    "FFmpegAVFoundation",
    "FFmpegDecodeSession",
    "MediaRecord",
    "parse_avfoundation_devices",
    "resolve_device",
    "AudioHealthSample",
    "CaptureHealthMonitor",
    "MediaHealthConsumer",
    "VideoHealthSample",
    "audio_health_sample",
    "video_health_sample",
    "TimestampNormalizer",
    "SimulatedCapture",
    "SimulatedTick",
]
