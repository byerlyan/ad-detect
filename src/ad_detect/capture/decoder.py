"""Supervised FFmpeg/PyAV decoding with one physical-device owner."""

from __future__ import annotations

import asyncio
import multiprocessing
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from queue import Empty
from typing import Any, Protocol, TypeAlias

from ad_detect.domain import AudioChunk, MediaStamp, VideoFrame

from .fanout import BoundedFanout, DropOldestChannel

MediaRecord: TypeAlias = AudioChunk | VideoFrame
Publisher: TypeAlias = Callable[[MediaRecord], None]
ErrorReporter: TypeAlias = Callable[[str], None]


class BlockingCaptureSession(Protocol):
    def run(self) -> None: ...

    def stop(self) -> None: ...


class CaptureCommandAdapter(Protocol):
    async def selected_devices(self) -> tuple[Any, Any]: ...

    def capture_command(self, video: Any, audio: Any) -> Sequence[str]: ...


class TimestampNormalizer:
    """Map stream PTS onto a monotonic session clock while retaining original PTS."""

    def __init__(self, session_id: str, *, origin_ns: int | None = None) -> None:
        self.session_id = session_id
        self.origin_ns = origin_ns if origin_ns is not None else time.monotonic_ns()
        self._first_pts_ns: int | None = None
        self._last_pts_ns: dict[str, int] = {}
        self._sequence = 0

    def stamp(
        self,
        pts: int | None,
        time_base: Fraction | None,
        *,
        stream_key: str = "default",
    ) -> MediaStamp:
        now_ns = time.monotonic_ns()
        discontinuity = pts is None or time_base is None
        if pts is None or time_base is None:
            pts_ns = max(0, now_ns - self.origin_ns)
        else:
            pts_ns = int(pts * time_base * 1_000_000_000)
        if self._first_pts_ns is None:
            self._first_pts_ns = pts_ns
        previous_pts = self._last_pts_ns.get(stream_key)
        if previous_pts is not None and pts_ns < previous_pts:
            discontinuity = True
        self._last_pts_ns[stream_key] = pts_ns
        captured_ns = self.origin_ns + max(0, pts_ns - self._first_pts_ns)
        stamp = MediaStamp(
            session_id=self.session_id,
            sequence=self._sequence,
            pts_ns=pts_ns,
            captured_monotonic_ns=captured_ns,
            discontinuity=discontinuity,
        )
        self._sequence += 1
        return stamp


class _PyAVDecodeSession:
    """Decode implementation that runs only inside the spawned child process."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        session_id: str,
        publish: Publisher,
        report_error: ErrorReporter,
    ) -> None:
        self.command = tuple(command)
        self.session_id = session_id
        self._publish = publish
        self._report_error = report_error
        self._stop = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self.stderr_tail: deque[str] = deque(maxlen=100)

    def _drain_stderr(self, stream: Any) -> None:
        for raw_line in iter(stream.readline, b""):
            self.stderr_tail.append(raw_line.decode(errors="replace").rstrip())

    def run(self) -> None:
        try:
            import av

            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            if self._process.stdout is None or self._process.stderr is None:
                raise RuntimeError("FFmpeg capture pipes were not created")
            stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(self._process.stderr,),
                name="ad-detect-ffmpeg-stderr",
                daemon=True,
            )
            stderr_thread.start()
            normalizer = TimestampNormalizer(self.session_id)
            with av.open(self._process.stdout, mode="r", format="nut") as container:
                for frame in container.decode():
                    if self._stop.is_set():
                        break
                    if isinstance(frame, av.VideoFrame):
                        stamp = normalizer.stamp(
                            frame.pts,
                            frame.time_base,
                            stream_key="video",
                        )
                        array = frame.to_ndarray(format="bgr24")
                        self._publish(
                            VideoFrame(
                                stamp=stamp,
                                width=frame.width,
                                height=frame.height,
                                pixel_format="bgr24",
                                pixels=array.tobytes(),
                            )
                        )
                    elif isinstance(frame, av.AudioFrame):
                        stamp = normalizer.stamp(
                            frame.pts,
                            frame.time_base,
                            stream_key="audio",
                        )
                        array = frame.to_ndarray()
                        self._publish(
                            AudioChunk(
                                stamp=stamp,
                                sample_rate_hz=frame.sample_rate,
                                channels=len(frame.layout.channels),
                                sample_format=frame.format.name,
                                pcm=array.tobytes(),
                            )
                        )
            returncode = self._process.wait(timeout=2.0)
            if returncode != 0 and not self._stop.is_set():
                tail = " | ".join(self.stderr_tail)
                raise RuntimeError(f"FFmpeg exited with status {returncode}: {tail}")
        except Exception as exc:
            if not self._stop.is_set():
                self._report_error(str(exc))
            raise
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)


def _decoder_process_main(
    command: tuple[str, ...],
    session_id: str,
    output: Any,
    stop_event: Any,
) -> None:
    """Spawn target: import PyAV away from OpenCV's separately bundled FFmpeg."""

    def publish(record: MediaRecord) -> None:
        output.put(("media", record))

    def report_error(message: str) -> None:
        output.put(("error", message))

    session = _PyAVDecodeSession(
        command,
        session_id=session_id,
        publish=publish,
        report_error=report_error,
    )

    def stop_when_requested() -> None:
        stop_event.wait()
        session.stop()

    threading.Thread(
        target=stop_when_requested,
        name="ad-detect-decoder-stop",
        daemon=True,
    ).start()
    try:
        session.run()
    except Exception:
        # The decoder already emitted the actionable error message.
        pass
    finally:
        output.put(("done", None))


class FFmpegDecodeSession:
    """Process-isolated decoder with a blocking parent-side queue bridge."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        session_id: str,
        publish: Publisher,
        report_error: ErrorReporter,
        process_queue_capacity: int = 16,
    ) -> None:
        if process_queue_capacity < 1:
            raise ValueError("process queue capacity must be positive")
        self.command = tuple(command)
        self.session_id = session_id
        self._publish = publish
        self._report_error = report_error
        self._capacity = process_queue_capacity
        self._stop = threading.Event()
        self._process: Any | None = None
        self._process_stop: Any | None = None

    def run(self) -> None:
        context = multiprocessing.get_context("spawn")
        output = context.Queue(maxsize=self._capacity)
        process_stop = context.Event()
        self._process_stop = process_stop
        process = context.Process(
            target=_decoder_process_main,
            args=(self.command, self.session_id, output, process_stop),
            name="ad-detect-pyav-decoder",
            daemon=True,
        )
        self._process = process
        process.start()
        error: str | None = None
        done = False
        try:
            while not done and not self._stop.is_set():
                try:
                    kind, payload = output.get(timeout=0.2)
                except Empty:
                    if not process.is_alive():
                        break
                    continue
                if kind == "media":
                    self._publish(payload)
                elif kind == "error":
                    error = str(payload)
                    self._report_error(error)
                elif kind == "done":
                    done = True
            process.join(timeout=2.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
            if error is not None and not self._stop.is_set():
                raise RuntimeError(error)
            if process.exitcode not in (0, None) and not self._stop.is_set():
                raise RuntimeError(f"decoder process exited with status {process.exitcode}")
        finally:
            output.cancel_join_thread()
            output.close()
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self._process_stop is not None:
            self._process_stop.set()
        process = self._process
        if process is not None and process.is_alive():
            process.join(timeout=2.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)


SessionFactory: TypeAlias = Callable[
    [Sequence[str], str, Publisher, ErrorReporter], BlockingCaptureSession
]


@dataclass(frozen=True, slots=True)
class CaptureSupervisorState:
    running: bool
    restart_count: int
    last_error: str | None


class CaptureSupervisor:
    """Start, restart, and stop exactly one blocking capture/decode session."""

    def __init__(
        self,
        adapter: CaptureCommandAdapter,
        *,
        session_id: str,
        queue_capacity: int,
        max_restarts: int = 3,
        restart_delay_seconds: float = 0.5,
        session_factory: SessionFactory | None = None,
    ) -> None:
        if max_restarts < 0 or restart_delay_seconds < 0:
            raise ValueError("capture restart limits are invalid")
        self.adapter = adapter
        self.session_id = session_id
        self.max_restarts = max_restarts
        self.restart_delay_seconds = restart_delay_seconds
        self.media = BoundedFanout[MediaRecord]()
        self._queue_capacity = queue_capacity
        self._factory = session_factory or self._default_factory
        self._active: BlockingCaptureSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._restart_count = 0
        self._last_error: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @staticmethod
    def _default_factory(
        command: Sequence[str],
        session_id: str,
        publish: Publisher,
        report_error: ErrorReporter,
    ) -> BlockingCaptureSession:
        return FFmpegDecodeSession(
            command,
            session_id=session_id,
            publish=publish,
            report_error=report_error,
        )

    def subscribe(self, name: str, capacity: int | None = None) -> DropOldestChannel[MediaRecord]:
        return self.media.subscribe(name, capacity or self._queue_capacity)

    def _publish_from_worker(self, record: MediaRecord) -> None:
        if self._loop is not None and not self._stopping:
            self._loop.call_soon_threadsafe(self.media.publish_nowait, record)

    def _report_error_from_worker(self, message: str) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_error, message)

    def _set_error(self, message: str) -> None:
        self._last_error = message

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # Fail fast on an incomplete selection, then resolve again for every
        # restart because AVFoundation indices can change after reconnects.
        await self.adapter.selected_devices()
        self._stopping = False
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name="capture-supervisor")

    async def _run(self) -> None:
        while not self._stopping:
            try:
                video, audio = await self.adapter.selected_devices()
                command = self.adapter.capture_command(video, audio)
                self._active = self._factory(
                    command,
                    self.session_id,
                    self._publish_from_worker,
                    self._report_error_from_worker,
                )
                await asyncio.to_thread(self._active.run)
                if not self._stopping:
                    self._last_error = "capture session stopped unexpectedly"
            except Exception as exc:
                if not self._stopping:
                    self._last_error = str(exc)
            finally:
                self._active = None
            if self._stopping or self._restart_count >= self.max_restarts:
                break
            self._restart_count += 1
            await asyncio.sleep(self.restart_delay_seconds * (2 ** (self._restart_count - 1)))

    async def wait(self) -> None:
        if self._task is not None:
            await self._task

    async def stop(self) -> None:
        self._stopping = True
        if self._active is not None:
            self._active.stop()
        if self._task is not None:
            await self._task
        self._task = None
        self._loop = None

    def state(self) -> CaptureSupervisorState:
        return CaptureSupervisorState(
            running=self._task is not None and not self._task.done(),
            restart_count=self._restart_count,
            last_error=self._last_error,
        )
