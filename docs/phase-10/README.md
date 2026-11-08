# Phase 10 reliability, optimization, and release plan

Status: planned and blocked for final acceptance on representative hardware,
data, and completed Phases 7–9. Short software fault tests may begin earlier,
but release and automatic-mute trials may not.

## Objective

Turn the feature-complete application into an operable, reproducible local
release with measured resource use, bounded failure behavior, recoverable data,
documented limitations, and separately consented shadow and automatic trials.
Optimization must preserve safety and evidence semantics.

## Entry criteria

- Phases 1–9 meet their software exit criteria with no unresolved critical
  safety defect.
- The target Mac, capture path, Apple TV/tvOS, television/audio route, storage,
  and network topology are inventoried.
- Representative labeled test sessions and a frozen safety gate exist.
- One compatible model/temporal configuration is selected through Phase 7, or
  the documented rules/hysteresis baseline is intentionally retained.
- Backup targets, retention preferences, and distribution license are chosen.

## Workstreams

### 1. Performance and capacity profile

Measure cold start, steady state, and degradation on the target Intel Mac:

- capture continuity, A/V skew, feature age, and queue drops;
- per-stage p50/p95/p99/max latency and end-to-end decision/action timing;
- process and child-process CPU, RSS, threads, file descriptors, and thermals;
- database latency/WAL/checkpoint behavior and daily growth;
- artifact/catalog sizes, fingerprint query latency, and retention throughput;
- dashboard/event-stream clients at expected and stress concurrency;
- optional detector/model load cost and sustained cadence.

Apply optimizations one at a time with before/after immutable reports. Follow
the documented degradation order: reduce previews and optional inference before
core health, expiry, policy, watchdog, or restoration behavior.

### 2. Twenty-four-hour soak

Run at least 24 hours after warm-up with capture, persistence, dashboard, shadow
decisions, and configured optional detectors active. Record every restart,
drop, gap, timeout, `UNKNOWN`, thermal event, database warning, and resource
sample. The run fails for unbounded queue, thread, descriptor, memory, database,
or disk growth even when detection metrics appear acceptable.

Repeat a shorter soak after every release-blocking fix. Preserve logs, config,
host inventory, and report digests as release evidence.

### 3. Fault-injection matrix

Exercise each fault in shadow and, where safe, simulated automatic mode:

| Fault | Required behavior |
| --- | --- |
| Video/audio loss, black/frozen frames, A/V skew | `UNKNOWN`; no mute renewal; restore if owned |
| FFmpeg exit/hang/malformed stream | bounded restart; gap event; no stale evidence reuse |
| Detector/model timeout or crash | exclude evidence; no capture backlog |
| Persistence slowdown, lock, corruption, or disk full | bounded spool/degradation; capture safety remains live |
| Apple TV disconnect/capability loss | no new mute; restoration attempt and prominent alert |
| Network change/client storm | dashboard degradation cannot block capture/control |
| Laptop sleep/wake and clock discontinuity | invalidate stale state; reconcile and restore safely |
| Thermal throttling/CPU pressure | disable optional work in declared order |
| Process signal, exception, or forced stop | watchdog/lease plus best-effort restore; outcome documented |
| Configuration/model activation failure | atomic rollback to last known-good state |

For failures where restoration is physically impossible, document the observed
state, lease limit, user alert, and recovery action rather than claiming success.

### 4. Data integrity, backup, and recovery

- define a consistent SQLite plus content-addressed-artifact backup procedure;
- verify hashes and referential integrity before accepting a backup;
- encrypt or clearly warn about pairing credentials and retained media;
- test restore onto a clean install and a separate directory;
- preserve append-only labels, model activation history, and setup/audit events;
- test interrupted migration and rollback without deleting labeled data;
- document which runtime-generated caches can be rebuilt or omitted.

### 5. Packaging and dependency inventory

Produce a reproducible Monterey installation path with pinned lock data and an
inventory containing package/model name, version, source, hash, license, and
purpose. Record the actual FFmpeg build/version and optional extras. Resolve the
project distribution license and every incompatible or unknown dependency/model
license before distribution.

Test clean install, upgrade, downgrade/rollback, uninstall boundaries, database
migration, credential permissions, service startup, and recovery on the target
OS. Do not bundle unsigned or unreviewed third-party binaries silently.

### 6. Operational diagnostics and documentation

Document:

- supported hardware/OS/dependency versions and capacity limits;
- loopback and secure-LAN startup, shutdown, backup, restore, and upgrade;
- capture permission and device-renaming recovery;
- Apple TV pairing, capability limitations, reconnect, and volume ownership;
- model/report provenance, activation, rollback, and failure recovery;
- retention/privacy defaults and deletion behavior;
- health/`UNKNOWN` reasons, watchdog and mute-lease alerts;
- HDCP behavior and the explicit prohibition on bypass attempts;
- unsupported step-only or unobservable volume configurations;
- known accuracy, channel, content, and latency limitations.

### 7. Release trials

Gate trials in this order:

1. deterministic CI and fault suite;
2. target-host passive capture and resource profile;
3. 24-hour detection-only/shadow soak;
4. frozen session-held-out evaluation and interval review;
5. release-candidate shadow trial during normal use;
6. explicit, separately consented, closely monitored automatic-mute trial;
7. rollback/recovery drill.

Automatic trial consent is not inherited from setup or a prior build. It
requires the exact release config/model digests, safe intervention capability,
successful restoration test, current backup, and acknowledged limitations.

## Release evidence package

- source commit and clean/dirty state;
- lockfiles and dependency/model/license inventory;
- database/config/report schema versions and migration results;
- target hardware, OS, tvOS, FFmpeg, capture, display/audio, and network profile;
- immutable evaluation and resource reports;
- 24-hour soak and complete fault-matrix results;
- backup/restore and install/upgrade/rollback records;
- security/threat-review status and known vulnerabilities;
- known limitations and explicit go/no-go decisions;
- automatic-trial consent and rollback record, if conducted.

## Verification plan

- CI unit, integration, replay, browser, migration, security, and packaging
  suites from all prior phases;
- fault injection with fakes plus selected real-process/device failures;
- resource leak assertions over short CI loops and the 24-hour hardware soak;
- deterministic installer tests on a clean Monterey host or restored image;
- backup corruption, partial restore, migration interruption, and rollback tests;
- session-held-out bootstrap confidence intervals and per-session reporting;
- manual operational drills performed from the published documentation.

## Exit criteria

- A 24-hour target-host soak stays within accepted CPU, RSS, disk, queue, thread,
  descriptor, database, and thermal budgets.
- Every failure-matrix row has observed, documented restoration/degradation
  behavior and no critical invariant violation.
- Frozen held-out evaluation meets the agreed safety gate with confidence
  intervals and no training-only claim.
- Install, upgrade, rollback, backup, and clean-host recovery pass on Monterey.
- Dependency, model, FFmpeg, and project licenses are resolved and inventoried.
- Release documentation states HDCP, privacy, security, accuracy, and volume
  limitations plainly.
- Automatic muting remains off unless a separate release-specific trial consent
  and every prerequisite gate are recorded.

## Stop-ship conditions

- any false mute caused by missing/stale capture or an expired decision;
- failure to restore an owned mute when the adapter remains capable;
- unbounded resource/storage growth or silent queue loss;
- deletion or corruption of labeled data, audit history, or active provenance;
- activation without authorization/audit or incompatible-model acceptance;
- exposed credential/secret or unauthenticated LAN mutation;
- unmet safety gate, unresolved critical license, or unrecoverable migration;
- any result presented as representative without immutable held-out evidence.

## Explicit non-goals

- no HDCP bypass;
- no cloud dependency or public-internet dashboard;
- no optimization that weakens `UNKNOWN`, deadlines, leases, watchdogs, or
  restoration;
- no promise of automatic muting on hardware without observable, safely
  restorable volume control.
