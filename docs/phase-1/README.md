# Phase 1 implementation status

Status: executable core implemented on 2026-11-08; target Intel Monterey and CI
matrix verification remain pending.

## Implemented

- Python 3.11 package, separated dependency extras, and a universal `uv.lock`;
- strict Pydantic TOML configuration with a small environment override surface;
- immutable `PROGRAM` / `COMMERCIAL` / `UNKNOWN` domain records and validation;
- observable bounded drop-oldest channels and independent fan-out consumers;
- deterministic simulated capture with valid and unavailable intervals;
- directional temporal hysteresis with fresh-evidence recovery after `UNKNOWN`;
- conservative expiring policy with maximum mute duration and re-entry cooldown;
- generation-based, idempotent simulated playback intervention;
- SQLite schema migration, canonical JSON events, configuration hashes, and WAL;
- causal runtime composition and reproducible session replay;
- FastAPI lifecycle, health/state/timeline endpoints, and responsive dashboard;
- secret-redaction helper;
- Ruff, strict mypy, and unit/integration/replay tests.

## Safety properties covered by tests

- invalid evidence cannot assign commercial probability;
- missing capture forces `UNKNOWN` and restoration;
- recovery from `UNKNOWN` requires a new commercial-entry evidence duration;
- maximum mute timeout cannot immediately re-mute;
- shadow mode never emits playback actions;
- stale action generations cannot reverse newer actuator state;
- slow fan-out consumers cannot block or grow without bound;
- replayed decisions and persisted event payloads are deterministic.

## Verification performed

```text
uv lock --check: 51 packages resolved
ruff check: passed
mypy --strict: 25 source files passed
pytest: 18 passed
installed CLI simulation: passed
local HTTP smoke test: dashboard, health, and state returned 200
```

The HTTP smoke test required local sandbox permission only to bind loopback; the
server remained bound to `127.0.0.1`.

## Deferred to later phases

- real FFmpeg/AVFoundation capture and synchronized media records;
- Apple TV discovery, pairing, feature inspection, and restoration testing;
- audio/video feature extraction, fingerprints, ML inference, and labeling;
- authenticated state-changing APIs and the setup wizard;
- real performance, accuracy, and Intel Monterey compatibility results.

These are intentionally not represented by fake adapters beyond the explicit
simulator.

