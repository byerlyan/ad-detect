# ADR 0003: FFmpeg NUT transport with a process-isolated PyAV decoder

Status: provisionally accepted, 2026-11-08; target-hardware benchmark required.

## Context

Audio and video must be opened once, retain timestamps, and feed multiple
consumers without letting inference block capture. OpenCV video polling does not
provide the required synchronized audio contract. Dropping arbitrary bytes from
an encoded subprocess pipe would corrupt the stream.

## Decision

One supervised FFmpeg process opens the selected AVFoundation video/audio pair
and writes a timestamped NUT stream containing BGR24 video and PCM audio. A
spawned child process imports PyAV and decodes that stream, then passes complete
immutable audio/video records through a bounded process queue. The parent fans
those records into bounded drop-oldest consumer queues.

Process isolation is required because the PyAV and OpenCV macOS wheels bundle
different FFmpeg libraries. Importing both in one process registers duplicate
AVFoundation Objective-C classes and may crash. OpenCV remains in the ML extra
and the PyAV child never imports it.

Device discovery and probing remain separate FFmpeg invocations. A restart
re-enumerates and resolves configured device names because indices can change.

## Consequences

The design preserves PTS and places dropping at safe frame/chunk boundaries.
Raw BGR transport may consume substantial memory bandwidth. Phase 2 hardware
profiling must compare accepted native formats and a low-latency compressed or
lower-resolution transport if the budget is exceeded. That optimization must
retain timestamp and capture-health semantics.

