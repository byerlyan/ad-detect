# Phase 4 audio fingerprinting status

Status: software baseline implemented on 2026-11-08. Representative commercial
captures, negative program sessions, codec-variant testing, catalog-scale
profiling, and target-hardware acceptance remain pending. No false-match or
recognition-rate claim is made.

## Implemented

- deterministic PCM decoding, gain normalization, linear 16 kHz resampling,
  STFT peak extraction, and versioned landmark hashes;
- normalized SQLite schema v4 reference/hash tables with indexed lookup;
- offset-cluster voting for arbitrary-position queries and approximate playback
  position;
- aligned-count, aligned-ratio, offset-dispersion, runner-up-margin, and minimum
  confidence guards;
- minimum duration, landmark density, unique-hash count, and frequency-diversity
  enrollment guards;
- enrollment restricted to an effective, reviewed `COMMERCIAL` label whose span
  contains the sample;
- duplicate detection with an explicit override rather than silent enrollment;
- active/disabled status and permanent removal management;
- identity and playback-position continuity across query windows;
- capture-health reset and zero commercial probability for invalid input;
- rolling five-second passive live queries whose evidence is persisted but is
  deliberately excluded from the Phase 3 decision path;
- fingerprint-only causal replay using the same metrics and post-window action
  timing as rules replay;
- local PCM-WAV CLI workflows and a read-only fingerprint review API.

## Developer workflow

Enrollment audio must be uncompressed 8-, 16-, or 32-bit PCM WAV and must match
the effective commercial label span duration.

```bash
.venv/bin/ad-detect fingerprint-enroll creative.wav \
  --name "Example Creative" --label-revision LABEL_REVISION_ID

.venv/bin/ad-detect fingerprint-query query.wav
.venv/bin/ad-detect fingerprint-list
.venv/bin/ad-detect fingerprint-status AD_ID disabled
.venv/bin/ad-detect fingerprint-status AD_ID active
.venv/bin/ad-detect fingerprint-remove AD_ID
```

`GET /api/v1/fingerprints` exposes reference metadata without adding an
unauthenticated mutation surface.

## Safety and interpretation

A match is commercial evidence, not a playback command. A first accepted match
waits for continuity confirmation. Missing matches remain `UNKNOWN`; they do not
prove regular programming. Unhealthy capture clears pending continuity. Phase 6
can now include confirmed fingerprint evidence in deadline-aware shadow fusion;
live capture still cannot control playback.

## Verification performed

```text
ruff check: passed
mypy --strict: 51 source files passed
pytest: 71 passed
deterministic extraction and silence rejection: tested
arbitrary-position identity and playback-position recovery: tested
synthetic gain and additive-noise tolerance: tested
unrelated-program negative query: tested
trusted-label and effective-revision enforcement: tested
duplicate, disable/enable, and removal behavior: tested
stationary-tone frequency-diversity rejection: tested
identity/position continuity and capture-health reset: tested
fingerprint-only causal replay semantics: tested
v1 -> v4 SQLite migration: tested
```

## Acceptance still pending

1. Enroll real reviewed commercials captured through the MS2130 and query clips
   beginning at varied positions.
2. Build negative program sessions across news, sports, promos, music, silence,
   bumpers, and repeated network material; use them to set false-match guards.
3. Measure gain changes, background noise, AAC/re-encode variants, edits,
   truncation, and modest time-scale changes rather than extrapolating from the
   synthetic unit fixture.
4. Measure identity accuracy, position error, continuity behavior, runner-up
   ambiguity, and false matches per hour per session.
5. Benchmark extraction and SQLite query p50/p95/p99, CPU, RSS, database size,
   and live queue behavior at the expected catalog size on the Intel Mac.
6. Tune thresholds only on development sessions, freeze them, and report unseen
   session results through the fingerprint replay baseline.
7. Add the authenticated visual review/enrollment workflow in the later setup
   and dashboard phases; Phase 4 currently supplies CLI management and read-only
   API review data.
