# Phase 3 rule baseline and data-collection status

Status: software baseline and live shadow composition implemented on 2026-11-08.
Representative labeling and target-hardware acceptance remain pending. No
detection-accuracy claim is made.

## Implemented

- one-second causal A/V window assembly with explicit audio, video, capture, and
  queue-gap missingness;
- versioned `classical-v1` NumPy features for energy, silence, spectral change,
  frequency bands, luma, black frames, edges, color, and scene cuts;
- an explainable `rules-v1` multimodal detector whose invalid or incomplete
  inputs emit `UNKNOWN` and zero commercial probability;
- directional hysteresis replay, reset at every session boundary;
- bounded in-memory media buffering by duration and byte count;
- SQLite schema v3 for feature windows, append-only three-way label revisions,
  boundary annotations, artifacts, and dataset manifests;
- content-addressed artifacts with oldest-first quota cleanup and automatic,
  permanent protection for every artifact span overlapping a human label;
- canonical, SHA-256-named dataset export, verification, and reimport preserving
  complete correction history;
- read-only feature and effective-label API views plus local CLI commands for
  labels, corrections, dataset transfer, and rules replay;
- a single-owner FFmpeg shadow pipeline through synchronized windows, feature
  persistence, rules evidence, hysteresis, and decision events;
- hard-coded shadow intervention for live capture, even if the loaded settings
  request automatic mode; this command never connects to Apple TV;
- capture discontinuity and bounded-queue drop diagnostics that invalidate the
  affected window, reset temporal feature history, and force `UNKNOWN`;
- session-start metadata recording for the config digest, selected capture
  devices, feature schema, and rules version; dataset exports carry this
  original per-session metadata.

## Causal evaluation semantics

Replay orders windows by `(session_id, start_ns)` and never reads a later window
when producing the current state. A decision becomes actionable only at the end
of its source window and is scored until the next tick. This prevents replay
from crediting the system with muting audio that had already played.

The report contains a time-weighted three-class confusion matrix, precision,
recall, F1, commercial mute coverage, false-muted program seconds per hour,
predicted-unknown time, mute/unmute latency, boundary error, missed commercial
spans, inference timing, wall/CPU time, and peak RSS. Empty denominators produce
zero, and boundary/latency means remain `null` when no span can be matched.

These metrics are plumbing validation only until multiple representative,
session-separated recordings have been labeled. Training-set or synthetic
results must not be presented as product accuracy.

## Developer workflow

All span times are integer nanoseconds.

```bash
# After selecting exact capture device names in configs/defaults.toml:
.venv/bin/ad-detect capture-devices
.venv/bin/ad-detect capture-probe
.venv/bin/ad-detect capture-shadow --session recording-001

.venv/bin/ad-detect label-add \
  --session recording-001 --start-ns START_NS --end-ns END_NS \
  --label commercial --boundary start --author reviewer

# Correct by appending a replacement; the earlier revision remains auditable.
.venv/bin/ad-detect label-add \
  --session recording-001 --start-ns START_NS --end-ns END_NS \
  --label program --author reviewer --supersedes REVISION_ID

.venv/bin/ad-detect dataset-export --session recording-001
.venv/bin/ad-detect dataset-import var/datasets/DIGEST.json
.venv/bin/ad-detect rules-replay --session recording-001
```

The local API exposes `GET /api/v1/features` and `GET /api/v1/labels` for the
current session by default. Label mutation remains a local CLI operation until
authenticated, CSRF-protected dashboard mutation is introduced.

## Verification performed

```text
ruff check: passed
mypy --strict: 43 source files passed
pytest: 62 passed
dataset hash verification and correction-chain reimport: tested
v1 -> v3 SQLite migration without event loss: tested
label-aware quota protection: tested
capture gaps -> UNKNOWN and hysteresis reset: tested
causal metric timing: tested
capture -> windows -> features -> evidence -> shadow decisions: tested
automatic configuration cannot enable intervention in live shadow: tested
bounded queue drops become persisted capture-gap diagnostics: tested
```

## Acceptance still pending

1. Run `capture-shadow` against the MS2130 and validate negotiated formats,
   timestamps, health states, queue behavior, and sustained resource use.
2. Retain transition clips from real input using a bounded encoded-media design;
   the current decoded-media buffer is tested but is not yet the live recorder.
3. Build a representative three-way labeled corpus across multiple recordings.
4. Replay with session-held-out review and inspect every false-muted program
   interval before selecting thresholds.
5. Measure sustained extraction/inference CPU, RSS, thermal behavior, queue
   drops, and artifact growth on the 2015 Intel MacBook Pro.
6. Keep automatic muting disabled; Phase 3 validates detection-only and shadow
   analysis, not production intervention safety.
