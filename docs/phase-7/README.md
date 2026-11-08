# Phase 7 training, evaluation, and model-registry plan

Status: software foundation implemented on 2026-11-08. Full baseline-matrix
replay, richer reports, target-host profiling, production authentication, and
representative held-out acceptance remain pending. No model is automatically
registered, selected, or activated.

## Objective

Make every quality claim and model transition reproducible, reviewable, and
reversible. A result must trace to an immutable dataset, grouped split, source
revision, configuration, detector/model artifacts, and evaluation code. A
trained candidate must remain inactive until a separate authorized action.

## Implemented foundation

- SQLite schema v5 evaluation-run, immutable test-partition, registered-model,
  active-pointer, and append-only activation-audit tables;
- content-addressed evaluation directories containing canonical manifest JSON,
  report JSON, and an escaped, offline HTML rendering;
- manifests binding dataset/config/report/model digests, locked grouped split,
  source revision and dirty state, schemas, command parameters, host profile,
  and timestamps;
- a final-test lock that rejects attempts to assign the same dataset digest to
  a different session partition or grouping definition;
- identical held-out rules replay across hysteresis, sliding-window, and HMM,
  plus candidate ML classification, causal replay, and per-session reports;
- explicit `accuracy_claim: null` and named unavailable baselines when a dataset
  lacks PCM fingerprint inputs or persisted aligned detector evidence;
- locally trusted bundle verification against a completed evaluation before
  registration;
- registration that never changes the active model pointer;
- explicit activation and rollback services requiring a typed authorization
  permission, nonempty reason, compatibility/integrity reload, configuration
  digest, and append-only success/failure audit;
- failed bundle validation leaves the active pointer unchanged;
- optional live resolution of the active registry model through
  `ml.use_registry=true`, mutually exclusive with a direct bundle path;
- safe CLI commands for evaluation, inactive registration, and listing;
- read-only evaluation/model APIs that omit local report and bundle paths.

Activation is intentionally not exposed through the unauthenticated API or a
convenience CLI. Phase 9 must supply the production authentication and session
boundary before operator activation becomes a normal UI workflow.

## Entry criteria

- Phase 3 label revisions, dataset manifests, and causal rules replay remain
  stable and migration-tested.
- Phase 4 fingerprint replay, Phase 5 candidate bundles, and Phase 6 temporal
  comparison expose versioned inputs and deterministic predictions.
- At least three independent recording groups exist for pipeline exercises.
  Representative selection claims require a larger corpus reserved before
  tuning.
- The project owner declares the primary safety objective before viewing final
  test results. The provisional priority is false-muted program seconds per
  hour, followed by commercial coverage and boundary latency.

## Earlier foundations

- append-only labels and content-addressed dataset export/import;
- deterministic recording-group train/calibration/test splitting;
- six inactive audio/video/multimodal tabular candidates;
- calibrated probabilities, immutable model bundles, and artifact hash checks;
- causal rules, fingerprint, ML, and temporal replay metrics;
- aggregate and per-session confusion, coverage, latency, CPU, and RSS fields;
- a default policy that never promotes a newly trained model.

These foundations reduce Phase 7 scope but are not a complete evaluation or
registry product.

## Current developer workflow

```bash
.venv/bin/ad-detect evaluation-run var/datasets/DATASET_DIGEST.json \
  --candidate models/candidates/BUNDLE_DIGEST \
  --output var/evaluations

# Registration verifies the bundle/evaluation relationship and remains inactive.
.venv/bin/ad-detect model-register models/candidates/BUNDLE_DIGEST \
  --evaluation EVALUATION_DIGEST --actor local-reviewer

.venv/bin/ad-detect model-list
```

Running `evaluation-run` from a dirty worktree is allowed for development but
is recorded in the immutable manifest and must not support a release claim.

## Workstreams

### 1. Immutable evaluation runs

Define an `EvaluationRunManifest` whose canonical digest binds:

- dataset manifest digest and complete label-revision set;
- explicit recording/rebroadcast groups and split assignment;
- a locked final-test designation that cannot be regenerated with a new seed;
- source commit and dirty-worktree status;
- complete validated configuration digest;
- feature, fingerprint, rules, temporal, policy, and report schema versions;
- every candidate bundle digest and dependency/runtime inventory;
- command arguments, start/end timestamps, host profile, and failure status.

Rerunning the same logical evaluation must create a new run record while
retaining the exact inputs. It must never overwrite an earlier report.

### 2. Full baseline matrix

Run the same session order, labels, action timing, and metric implementation for:

- rules only;
- fingerprints only;
- audio ML, video ML, and multimodal ML;
- available detector combinations;
- hysteresis, sliding-window, and HMM temporal strategies;
- the conservative decision policy in detection-only replay.

Reports must include `UNKNOWN`, invalid/expired evidence, dropped work, and
unlabeled time rather than filtering them out. Per-session output is mandatory;
aggregate output alone can hide channel-specific failures.

### 3. Resource profiler

Add repeatable cold-load and warm-replay measurements for wall time, CPU time,
peak and steady RSS, inference p50/p95/p99/max, artifact size, queue drops, and
throughput. Record the CPU-percentage convention and target host identity.
Profile reports are evidence artifacts, not live tuning inputs.

### 4. JSON and HTML reports

Produce a canonical JSON report and a self-contained local HTML rendering with:

- aggregate and per-session confusion matrices;
- precision, recall, F1, commercial coverage, and false-muted program time;
- mute/unmute latency distributions and boundary-error plots;
- calibration reliability, Brier score, log loss, and `UNKNOWN` coverage;
- inference/resource distributions and dropped/expired work;
- paired deltas against rules and the currently active model, if one exists;
- links or identifiers for reviewable false-positive and missed-ad intervals;
- prominent limitations and `accuracy_claim: null` until the declared gates are
  met.

HTML must escape all dataset text and remain viewable without a network
connection. It must not embed retained media unless explicitly requested.

### 5. Immutable model registry

Add registry records for bundle digest, compatibility contract, dataset and
evaluation digests, registration actor/time, status, and notes. Keep an
append-only activation audit containing previous and requested model, actor,
reason, timestamp, configuration revision, and outcome.

Required lifecycle:

```text
candidate artifact -> registered/inactive -> explicitly activated
                                      \-> rejected/retired
active -> rollback to a previously compatible registered bundle
```

Registration must verify hashes, trusted local provenance, exact dependency
versions, feature/aggregation schemas, ordered columns/classes, and evaluation
presence. Registration never changes the active pointer.

Activation and rollback must be serialized, transactional, schema-compatible,
and auditable. The service boundary must require an authorized actor context;
no unauthenticated network route is added in this phase. Phase 9 supplies the
production LAN authentication/session boundary.

### 6. Commands and service boundaries

Planned commands or equivalent internal services:

```text
evaluation-run DATASET --candidate BUNDLE... --output REPORTS
evaluation-show RUN_DIGEST
model-register BUNDLE --evaluation RUN_DIGEST
model-list
model-activate MODEL_ID --reason TEXT
model-rollback ACTIVATION_ID --reason TEXT
```

Destructive replacement is not supported. Local activation commands require an
explicit confirmation, actor identity, and authorization provider; automated
training never invokes them.

## Persistence and migration

Use new normalized SQLite tables for evaluation runs, registered models, and
append-only activation events. Large report and model artifacts remain in
content-addressed filesystem directories. Database rows store hashes, paths,
sizes, schema versions, and provenance. Migrations must preserve all prior
events, labels, fingerprints, and dataset records.

## Verification plan

- unit tests for canonical digests, compatibility checks, final-test locking,
  and paired metric calculations;
- leakage tests proving session and rebroadcast groups never cross splits;
- mutation tests proving changed config, model, labels, code state, or report
  schema changes the run identity;
- integration tests for register-without-activate, transactional activation,
  incompatible rejection, failed-load rollback, and audit completeness;
- golden JSON/HTML report tests including escaping and missing metrics;
- deterministic replay tests across the full baseline matrix;
- profiler tests with fake clocks/resource samplers so CI remains stable;
- recovery tests for interrupted report writes and database transactions.

## Verification performed

```text
ruff check: passed
mypy --strict: 64 source files passed
pytest: 101 passed
v1 -> v5 migration without event loss: tested
content-addressed evaluation manifest/report/HTML: tested
locked final-test partition mismatch rejection: tested
all temporal strategies and one ML bundle in held-out evaluation: tested
register without activation: tested
unauthorized activation rejection: tested
authorized activation and rollback audit: tested
tampered active candidate fails closed without pointer change: tested
read-only APIs redact local artifact paths and registration actor: tested
```

## Exit criteria

- One command reconstructs every required metric from immutable inputs.
- Aggregate and every-session reports include all classes, missingness, dropped
  work, and resources.
- Final-test assignment is immutable and demonstrably leakage-safe.
- Registering or training a bundle cannot activate it.
- Activation requires an authorized actor contract, explicit reason, compatible
  schema, successful load check, and append-only audit event.
- Rollback is transactional and restores a previously verified bundle.
- Representative held-out results are reported without training-only claims.

## Data-dependent acceptance still pending

1. Collect and freeze representative independent recordings before setting the
   final numerical safety gate.
2. Resolve O8, O9, O10, and O14 from measured candidate, temporal, embedding,
   and user-tolerance evidence.
3. Inspect every false-muted program interval in the final grouped test.
4. Repeat resource profiling on the target Intel Mac rather than substituting a
   development-host result.

## Explicit non-goals

- no automatic model promotion;
- no cloud training, telemetry, or hosted report service;
- no public or unauthenticated activation endpoint;
- no accuracy claim inferred from synthetic or training sessions.
