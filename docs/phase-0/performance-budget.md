# Performance budget

These are Phase 1/2 acceptance targets, not benchmark results. They assume a
2015 Intel MacBook Pro whose exact CPU, RAM, thermals, storage, and capture mode
remain to be inventoried. Budgets are measured over a two-hour hardware soak
after warm-up, on AC power, with the dashboard connected.

## End-to-end targets

| Resource/latency | Target | Hard guardrail | Measurement |
| --- | ---: | ---: | --- |
| Capture continuity | >= 99.9% valid intervals | No silent gaps | PTS/sequence audit |
| A/V skew after normalization | p95 <= 50 ms | 150 ms invalidates window | Synchronized-window logs |
| Capture-to-feature age | p95 <= 500 ms | 1,500 ms stale | Monotonic timestamps |
| Classical feature extraction | p95 <= 100 ms per 1 s tick | 300 ms | Per-stage timer |
| Core ML inference | p95 <= 50 ms per fused window | 200 ms | Per-model timer |
| Optional embedding inference | p95 <= 400 ms at configured cadence | Must not backlog | Worker metrics |
| Temporal + policy decision | p95 <= 20 ms | 100 ms | Per-tick timer |
| Action dispatch after eligible decision | p95 <= 250 ms, excluding device acknowledgement | 1 s | Decision/action correlation |
| Dashboard state age | p95 <= 1 s | 3 s | Client/server timestamp |
| Sustained process CPU | <= 150% of one core-equivalent aggregate target | <= 250% during optional inference | `psutil`, 1 s samples |
| Resident memory | <= 1.0 GiB core | <= 1.5 GiB with optional embeddings | RSS time series |
| Core database growth | <= 100 MiB/day without media | Alert at configured quota | Store metrics |
| Unlabeled media | User quota, default minimal/off | Never exceed quota by > one active clip | Retention audit |

CPU percentage convention must be recorded (for example, `100%` equals one
fully occupied logical CPU). Thermal throttling and dropped windows are reported
alongside latency; a fast result achieved by dropping evidence is not a pass.

## Sampling budget

Initial settings to validate:

| Stream | Capture | Live analysis | Retained review artifact |
| --- | --- | --- | --- |
| Video | Negotiated 720p/1080p at ~30 fps | Downscaled 320-480 px width at 2 fps; embeddings 0.5-1 fps | 480p low-rate H.264 or thumbnails, configurable |
| Audio | 48 kHz stereo from device | Mono 16 kHz for features/fingerprint; 1 s chunks with overlap as needed | Mono AAC/FLAC, configurable |
| Fusion | N/A | 1 Hz causal tick | Every decision/event |

The capture process reads at device rate even though analysis samples less
often. This avoids driver buffering surprises and lets the fan-out choose the
proper time-aligned sample.

## Latency objective

Technical processing latency is only part of mute-start latency. The conservative
policy intentionally waits for sustained evidence. The initial product target is:

- median mute start within 2.5 s of a labeled commercial boundary;
- p95 unmute no later than 1.5 s after labeled program return;
- false-muted program seconds/hour is the primary safety gate and takes
  precedence over faster mute starts.

These values are hypotheses until real sessions are labeled. Evaluation reports
must separate capture/inference/action latency from policy evidence duration.

## Degradation policy

When work exceeds budget, degrade in this order:

1. reduce dashboard preview rate;
2. skip optional OCR;
3. reduce optional visual embedding cadence;
4. disable optional embeddings with an explicit health event;
5. reduce classical video sampling while preserving capture health;
6. enter detection-only/`UNKNOWN` if core evidence deadlines still fail.

Never allow an inference backlog to increase evidence age indefinitely. Never
drop health monitoring, decision expiry, watchdog restoration, or manual control.

## Benchmark matrix

Each optional runtime/model is benchmarked with the same recorded fixture and
live capture:

- cold load time, model/RSS increment, artifact size;
- p50/p95/p99 inference time and throughput;
- total application CPU/RSS and thermals over two hours;
- dropped/expired windows;
- metric gain in session-held-out replay;
- installation success from a clean Monterey/Python 3.11 environment.

An optional model is accepted only if it meets both the runtime budget and the
quality gate. Compatibility alone is not a reason to ship it.

