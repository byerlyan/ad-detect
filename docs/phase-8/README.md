# Phase 8 guided setup and configuration UI plan

Status: planned. Backend adapters and configuration validation exist, but the
resumable setup state machine, transactional settings service, browser workflow,
and diagnostics package are not implemented.

## Objective

Let a new user reach a safe shadow-ready state without editing TOML or using the
CLI. Setup must explain limitations, validate each dependency, persist progress,
and refuse automatic mute until every readiness and consent gate succeeds.

## Entry criteria

- Phase 2 capture and Apple TV adapters expose discovery, probing, pairing,
  capability, reconnect, and diagnostic results through typed services.
- Phase 3 capture health and shadow runtime can run without intervention.
- Phase 6 exposes strategy, override, lease, and safety state.
- Phase 7 provides read-only model registry/readiness services. Activation UI
  remains unavailable until its authorization contract is satisfied.
- Loopback remains the default bind address throughout setup.

## Persisted setup state

Use a backend-owned state machine rather than browser navigation state:

```text
WELCOME
  -> VIDEO_PROBED
  -> AUDIO_PROBED
  -> APPLE_TV_PAIRED or INTERVENTION_SKIPPED
  -> MODE_SELECTED
  -> CALIBRATION_RECORDED or CALIBRATION_SKIPPED
  -> READINESS_CHECKED
  -> SHADOW_VALIDATED
  -> AUTO_MUTE_CONSENTED (optional terminal gate)
```

The original product flow has nine user-facing screens. Some screens map to one
persisted gate, and optional steps record an explicit skip reason. Refresh and
restart resume at the first incomplete or invalidated gate.

Every completed gate stores its input configuration revision, result digest,
completion time, and actor/session. Hardware, model, or safety-setting changes
invalidate only downstream dependent gates.

## User-facing workflow

### 1. Welcome

Explain local processing, supported hardware, commercial detection limits,
HDCP/black-frame behavior, optional retention, and the difference between
detection-only, shadow, and automatic modes.

### 2. Video discovery and test

Enumerate AVFoundation video devices by stable observed attributes, show a
short server-generated preview, test negotiated format/timestamps, and report
black/frozen frames. Never silently choose an ambiguous device.

### 3. Audio discovery and test

Enumerate audio endpoints, display levels without retaining content, verify
sample rate/channels/timestamps, and measure A/V skew. Missing or unsynchronized
audio blocks multimodal-readiness claims.

### 4. Apple TV discovery and pairing

Discover devices, display capabilities, perform guided PIN pairing, and persist
credentials server-side with user-only permissions. Pairing secrets and stored
credentials never appear in API responses, logs, diagnostics, or browser
storage. Detection-only/shadow setup may explicitly skip this step.

### 5. Detection mode

Select detection-only or shadow by default. Automatic mode appears only as a
future gated outcome, never as the initial selection. Explain each mode and
which data or control it can change.

### 6. Optional visual calibration

Verify crop/aspect, black-frame thresholds, frozen-frame behavior, and analysis
sampling using non-destructive previews. Calibration records its source device
signature and expires when relevant hardware settings change.

### 7. Model and fingerprint readiness

Show active/inactive model compatibility, feature schema, evaluation status,
fingerprint catalog status, missing optional detectors, and plain-language
explanations of rules, fingerprints, ML, and temporal smoothing.

### 8. Shadow validation

Run a bounded shadow session and show capture validity, `UNKNOWN` reasons,
detector contributions, hypothetical actions, lease/watchdog checks, queue
drops, and resource warnings. Define a minimum duration, but do not imply that
one short run proves detection accuracy.

### 9. Explicit automatic-mute consent

This gate is unavailable unless shadow validation, safe intervention
capabilities, restoration test, model/config compatibility, and required
security controls pass. Present maximum duration, override, lease, watchdog,
known limitations, and a separate explicit consent action. Failure reverts to
shadow rather than partially enabling automation.

## Configuration service

Provide one versioned schema to both TOML and UI. The browser edits a draft;
the backend validates the complete candidate, computes dependent invalidations,
and commits one configuration revision transactionally.

Basic settings:

- capture device selection;
- Apple TV selection;
- detection mode and shadow state;
- conservative/balanced/aggressive sensitivity profile;
- active registered model display;
- retention preference and automatic-mute consent state.

Advanced settings:

- capture format and sampling cadence;
- rule, fingerprint, ML, evidence-deadline, temporal, and policy parameters;
- rolling buffer, artifact quota, retention, and training options;
- diagnostic verbosity and optional detector enablement.

Profiles expand to explicit validated values. Switching profiles previews the
diff and never overwrites unrelated advanced values without confirmation.

## Backend/API plan

Keep hardware ownership in backend services. Browser endpoints initiate bounded
jobs and return typed status; they never open capture devices themselves.
Long-running discovery, probe, pairing, and shadow validation jobs expose a
job ID, progress, cancellation, timeout, and terminal diagnostic result.

State-changing setup endpoints require same-origin and anti-CSRF protection even
on loopback. Use short-lived bootstrap/setup sessions and do not add LAN access
before Phase 9 authentication is complete.

## Diagnostics export

Export a local archive containing configuration with secrets redacted, device
and capability summaries, versions, recent health/diagnostic events, setup gate
results, and optional report digests. Exclude pairing credentials, cookies,
password hashes, raw media, and labels unless separately selected. Test
redaction against every secret-bearing schema field.

## Frontend decision checkpoint

Resolve O11 with a small prototype before committing to a build system. The
default remains a small TypeScript bundle with no SSR. Evaluate bundle size,
Monterey-compatible tooling, form/schema reuse, accessibility, testability, and
long-term maintenance. Record the selection in an ADR.

## Verification plan

- state-machine tests for every valid transition, illegal transition, optional
  skip, restart/resume, and dependency invalidation;
- fake capture/Apple TV/model services for deterministic browser and CI tests;
- transactional configuration tests proving invalid drafts change nothing;
- permission, pairing cancellation/timeout, ambiguous-device, and reconnect
  tests;
- diagnostics snapshot tests proving secret and media redaction;
- accessibility tests for labels, focus order, errors, keyboard navigation, and
  color-independent state;
- Playwright happy-path and recovery flows at desktop and phone viewports;
- tests proving automatic consent cannot be posted around failed prerequisites.

## Exit criteria

- A new user can reach a valid shadow session without CLI or file editing.
- Refresh/restart resumes safely, and hardware/config changes invalidate only
  dependent gates.
- Every detector and mode has a plain-language explanation.
- Unsafe or malformed settings are rejected atomically with actionable errors.
- Pairing and application secrets never reach browser persistence or diagnostic
  output.
- Automatic mute remains impossible until all readiness, security, restoration,
  shadow, and consent gates succeed.

## Hardware-dependent acceptance still pending

1. Complete the workflow on a clean Monterey installation with the real MS2130
   and Apple TV.
2. Validate device identity across reboot, USB-port change, sleep/wake, and
   permission denial.
3. Resolve O1-O5, O11, O13, and O15 from measured setup evidence.
4. Run user testing for terminology, error recovery, consent comprehension, and
   phone-assisted setup before declaring normal setup CLI-free.

## Explicit non-goals

- no public internet exposure;
- no automatic model training or activation;
- no client-side storage of pairing credentials;
- no claim that successful capture setup means protected content is available.
