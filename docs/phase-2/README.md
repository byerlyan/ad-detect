# Phase 2 integration status

Status: software integration implemented on 2026-11-08; physical MS2130,
Apple TV, Intel Monterey, and extended-soak acceptance remain pending.

## Capture implementation

- FFmpeg version detection and AVFoundation audio/video device enumeration;
- robust parsing independent of FFmpeg's changing pointer prefixes;
- exact device-name or current-index resolution with ambiguity errors;
- short synchronized A/V probe that succeeds only when both streams negotiate;
- one FFmpeg process opening one selected video/audio input pair;
- timestamp-preserving NUT transport with BGR24 video and PCM audio;
- spawned PyAV decoder process isolated from both the async event loop and
  OpenCV's separately bundled FFmpeg libraries;
- original PTS plus monotonic session stamps and per-stream discontinuity checks;
- immutable `AudioChunk` and `VideoFrame` records;
- bounded per-consumer fan-out after decoding;
- capped restart supervision with exponential backoff and device re-resolution;
- health monitoring for missing, stale, black, frozen, and A/V-skewed capture.

Protected, black, frozen, missing-audio, stale, and desynchronized input produces
unhealthy capture state. The existing temporal/policy path turns that state into
`UNKNOWN`; it cannot become positive commercial evidence.

## Apple TV implementation

- pyatv 0.18.0 isolated behind the intervention port;
- discovery with stable identifiers and protocol reporting;
- guided begin/finish/cancel pairing primitives;
- pyatv `FileStorage` with user-only credential-file permissions;
- connection listener and bounded reconnect with exponential backoff;
- runtime feature inspection for volume read/set/up/down;
- automatic mute allowed only when volume is both observable and absolutely
  settable;
- exact pre-mute volume restoration owned by one mute lease;
- generation-based idempotency and stale-command rejection;
- manual volume-change detection that avoids overwriting the user's choice;
- restoration on disconnect/shutdown and after reconnect when possible;
- simulated and Apple TV implementations selected through the same port.

Step-only volume control is reported but deliberately not considered safe for
automatic mode. Detection and shadow mode remain usable when absolute volume is
unavailable.

## Developer diagnostics

```bash
.venv/bin/ad-detect capture-devices
.venv/bin/ad-detect capture-probe
.venv/bin/ad-detect appletv-devices
```

`capture-probe` uses the selected `capture.video_device` and
`capture.audio_device` in `configs/defaults.toml`. The later setup wizard will
replace this developer configuration workflow.

## Verification performed

```text
FFmpeg version diagnostic: passed (local FFmpeg 9.0.2)
AVFoundation enumeration parser and probe failure behavior: tested
single-owner supervisor, bounded fan-out, restart limit: tested
capture-health failure states: tested
pyatv discovery/pairing/volume/reconnect behavior: mock-tested
ruff check: passed
mypy --strict: 30 source files passed
pytest: 39 passed
```

The local sandbox returned no AVFoundation devices, so no negotiation or media
frames were fabricated. Apple TV LAN discovery was not run automatically.

During native dependency validation, importing PyAV and OpenCV in one macOS
process produced duplicate AVFoundation class warnings because their wheels
bundle different FFmpeg libraries. The decoder was therefore moved to a spawned
process. OpenCV also moved from the `capture` extra to the `ml` extra.

## Hardware acceptance still required

1. Inventory the target Intel Mac CPU/RAM/storage and install the locked Python
   environment on Monterey.
2. Confirm the MS2130 device names, stable modes, pixel format, audio endpoint,
   and 48 kHz behavior.
3. Run the two-hour PTS/skew/drop/CPU/RSS/thermal soak and tune raw transport if
   it exceeds the performance budget.
4. Exercise black source, audio removal, USB removal/reconnect, FFmpeg crash,
   sleep/wake, and device-index changes.
5. Pair the real Apple TV protocols and record pyatv capability output.
6. Verify which physical endpoint volume controls, manual volume changes, exact
   restoration, disconnect, process termination, and reconnect behavior.
7. Keep automatic muting disabled unless every restoration test passes.

