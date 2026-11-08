# ADR 0006: Local landmark audio fingerprints

Status: accepted as a provisional Phase 4 baseline on 2026-11-08.

## Context

Recurring advertisements must be recognized from arbitrary subclips without
cloud services or GPU hardware. A whole-file checksum or perceptual hash cannot
reliably align a query that begins partway through a creative. A fingerprint
match is strong evidence, but false matches could mute regular programming.

## Decision

- Decode PCM deterministically, normalize gain, and resample to 16 kHz mono.
- Extract local STFT peaks and pair them into versioned `(f1, f2, delta_t)`
  landmark hashes.
- Store hashes in normalized SQLite tables keyed by reference and anchor time.
- Match by voting for consistent `reference_time - query_time` offsets.
- Require aligned count, query ratio, offset dispersion, runner-up margin,
  confidence, identity continuity, and playback-position continuity.
- Reject short, sparse, spectrally narrow, or likely duplicate enrollment.
- Permit enrollment only from the effective revision of an explicit commercial
  label. References retain their source session/span, label revision, and
  fingerprint version.
- Treat missing matches as `UNKNOWN`, not `PROGRAM`. Capture-health failure
  resets continuity and cannot emit commercial probability.
- Run live matching passively in shadow capture. Fingerprint evidence is logged
  independently and does not affect decisions until Phase 6 fusion evaluation.

## Consequences

The index remains local, inspectable, and replaceable without changing detector
evidence contracts. SQLite is adequate for establishing correctness and a
measured catalog-size baseline; a different index is allowed later if query p95
misses the resource budget.

Thresholds are provisional. Synthetic gain/noise tests validate plumbing but do
not establish real false-match behavior. Negative program sessions, actual
re-encodes, edited creatives, and catalog-scale profiling remain mandatory.

## Rollback conditions

Replace the extractor or index only through a new fingerprint version and a
paired replay report. Existing references must remain queryable by their
recorded version or require explicit reviewed re-enrollment.
