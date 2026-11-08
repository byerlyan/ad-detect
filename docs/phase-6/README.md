# Phase 6 temporal fusion and decision-policy status

Status: software baseline implemented on 2026-11-08. Representative
unseen-session strategy selection, fused-detector threshold tuning, secured
operator controls, and target-hardware shadow soak remain pending. Automatic
live intervention is not enabled.

## Implemented

- one deadline-aware evidence observation shared by every temporal strategy;
- exclusion of invalid, future, and expired detector results, plus hard
  `UNKNOWN` gates for unhealthy capture or no remaining valid evidence;
- equal provisional fusion of available valid rules, fingerprint, and optional
  ML probabilities with detector contribution and version explanations;
- directional hysteresis, recency-weighted sliding-window fusion, and a
  transparent three-state HMM behind one temporal classifier contract;
- temporal reset after unhealthy capture or missing valid evidence so pre-gap
  observations cannot authorize recovery;
- identical causal replay and Phase 3 metrics for all three strategies through
  `ad-detect temporal-replay`;
- a separate conservative decision policy supporting detection-only, shadow,
  and explicitly consented automatic modes;
- break state, directional entry/recovery, maximum mute duration, and re-entry
  cooldown;
- immediate runtime manual-program override that restores an owned mute and
  prevents renewal while active;
- an acknowledged wall-clock mute lease renewed only by fresh commercial
  decisions, plus watchdog restoration if decisions stop;
- shutdown restoration, idempotent generations, and stale-command rejection;
- live shadow fusion of available detector evidence while the capture pipeline
  continues to force simulated shadow intervention regardless of configuration;
- runtime state visibility for manual override and mute-lease activity.

## Temporal strategies

`hysteresis` remains the default. It has the smallest state and the clearest
directional thresholds. `sliding_window` averages recent observations with a
configurable decay. `hmm` applies a configurable stay probability to a
three-state transition model and emits its posterior in every explanation.

Configuration lives in `[temporal]`:

```toml
[temporal]
strategy = "hysteresis" # hysteresis | sliding_window | hmm
evidence_deadline_ms = 1500
sliding_window_ticks = 5
sliding_decay = 0.8
hmm_stay_probability = 0.92
```

The lease and watchdog are independent policy safeguards:

```toml
[detection]
maximum_mute_seconds = 300
reentry_cooldown_seconds = 10
mute_lease_ms = 3000
watchdog_interval_ms = 250
```

## Developer workflow

Replay one or all labeled sessions through identical rules evidence and all
three temporal strategies:

```bash
.venv/bin/ad-detect temporal-replay --session recording-001
.venv/bin/ad-detect temporal-replay
```

The JSON result contains one complete causal metric report per strategy. Do not
change `temporal.strategy` based on training sessions alone. Predeclare the
selection objective, freeze settings, and compare on untouched recording
groups.

## Safety behavior

Invalid, expired, and future-dated detector results cannot contribute support.
Invalid or stale capture, A/V skew beyond the limit, no valid detector before
the deadline, manual program override, maximum mute duration, lease expiry, and
shutdown prevent mute initiation or renewal. When an application-owned mute may
exist, these hard-gate paths request restoration.

The live FFmpeg composition still constructs a shadow-only simulated
intervention. Phase 6 validates state and recommendations; it does not authorize
real automatic Apple TV control.

## Verification performed

```text
ruff check: passed
mypy --strict: 62 source files passed
pytest: 98 passed
hysteresis directional entry/recovery and post-gap fresh entry: tested
sliding-window aging and HMM normalized posterior/reset: tested
future/expired/invalid evidence exclusion and all-missing -> UNKNOWN: tested
detector contribution explanations and evidence versions: tested
all three strategies over identical causal replay windows: tested
manual override -> immediate restore and no renewal: tested
maximum mute duration and cooldown: tested
mute lease expiry -> watchdog restore: tested
shutdown while muted -> restore: tested
shadow mode suppression and intervention idempotency: tested
```

## Acceptance still pending

1. Freeze a representative collection of independent, three-way labeled test
   sessions and predeclare false-muted program seconds per hour as the primary
   safety gate, with coverage and boundary latency as secondary objectives.
2. Replay rules, fingerprint, selected ML candidates, and their combinations
   through every temporal strategy. Review results per session and inspect every
   false-muted program interval.
3. Tune detector weighting, deadlines, sliding decay/window, and HMM transitions
   only on development sessions; do not tune against the final grouped test.
4. Retain hysteresis unless a more complex strategy materially improves the
   frozen objective and calibration without violating latency/resource budgets.
5. Run a sustained passive shadow soak on the target Intel Mac covering capture
   gaps, detector timeouts, process stalls, Apple TV disconnects, manual volume
   changes, override, lease expiry, and shutdown.
6. Add authenticated, CSRF-protected operator controls and an auditable override
   lifecycle in later dashboard phases; do not expose manual mutation through
   the current unauthenticated local API.
7. Keep automatic muting disabled until model/strategy selection, shadow-soak
   gates, intervention capability checks, and explicit user consent are all
   complete.
