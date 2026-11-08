# ADR 0008: Deadline-aware temporal fusion and mute leases

Status: accepted as a provisional Phase 6 baseline on 2026-11-08.

## Context

Rules, fingerprints, and optional ML produce independent three-class evidence
at different cadences. Missing, late, invalid, or unhealthy input must never be
treated as commercial support. Temporal smoothing must be compared causally,
while playback action policy remains independently safety-asymmetric. A stalled
runtime must not leave an application-owned mute active indefinitely.

## Decision

- Convert each tick into one deadline-aware observation. Reject future or
  expired evidence, exclude invalid detectors, and force `UNKNOWN` when capture
  health fails or no valid evidence remains.
- Average valid detector probabilities equally for the provisional fusion
  baseline and record each detector's probability contribution and version.
- Support three replaceable temporal classifiers behind one contract:
  directional hysteresis, a recency-weighted sliding window, and a transparent
  three-state hidden Markov model.
- Reset temporal history on invalid capture or absent valid evidence. Recovery
  must accumulate fresh observations rather than reuse pre-gap state.
- Keep directional hysteresis as the configured default until an immutable,
  unseen-session comparison demonstrates that a more complex approach improves
  the declared safety objective.
- Keep temporal classification separate from the decision policy. Only explicit
  automatic mode plus consent may emit actuator commands; shadow and
  detection-only modes never do.
- Treat `UNKNOWN`, manual program override, and maximum mute duration as
  restoration paths that cannot initiate or renew a mute. Expired evidence is
  excluded and forces this path when no fresh valid detector remains.
- Grant an acknowledged mute a short wall-clock lease. Renew it only from fresh
  commercial decisions. A background watchdog restores when the lease expires,
  and shutdown always requests restoration.
- Run all three strategies through identical causal windows and scoring via the
  `temporal-replay` comparison command.

## Consequences

Detector failures degrade evidence availability instead of producing commercial
support. Explanations expose both hard gates and contributing probabilities.
The wall-clock lease covers a stalled decision loop independently of capture
timestamps, while maximum mute duration remains a separate policy limit.

Equal detector weighting and fixed HMM transitions are provisional, inspectable
baselines rather than tuned claims. Fingerprint and optional ML evidence may
participate in live shadow fusion, but the live capture pipeline still replaces
intervention settings with simulated shadow mode.

Manual override exists at the runtime contract but is not exposed through an
unauthenticated mutation API. A secured operator workflow belongs to later UI
phases.

## Rollback conditions

Retain hysteresis if sliding-window or HMM replay does not materially improve
the predeclared unseen-session objective. Change weights, transitions, or the
selected strategy only through versioned configuration and a paired causal
report. Never disable the health gates, decision expiry, mute lease, watchdog,
or shutdown restoration to improve headline coverage.
