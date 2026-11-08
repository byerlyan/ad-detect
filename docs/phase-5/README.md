# Phase 5 ML inference status

Status: software baseline implemented on 2026-11-08. Representative labeled
recordings, unseen-session candidate comparison, calibration reliability,
target-Intel-Mac profiling, and model selection remain pending. No detection
accuracy claim is made.

## Implemented

- causal 1, 3, 10, and 30 second aggregation with mean, standard deviation,
  quantiles, slope, transition count, coverage, and explicit missingness;
- time-overlap label assignment without reading future feature windows;
- deterministic recording-group train/calibration/test splits with optional
  grouping of rebroadcast variants and hard disjointness checks;
- audio-only, video-only, and early-fusion candidates for logistic regression
  and histogram gradient boosting;
- training-only preprocessing and held-out-session multinomial probability
  calibration when every class is represented;
- accuracy, multiclass Brier score, log loss, expected calibration error, and
  three-way confusion matrices;
- immutable content-addressed bundles binding preprocessing, model, calibrator,
  schemas, columns/classes, dataset digest, metrics, and dependency versions;
- strict bundle integrity, feature-schema, contract, and exact dependency
  checks before locally trusted deserialization;
- deadline-bounded inference whose capture, schema, timeout, or model failures
  emit invalid `UNKNOWN` evidence and zero commercial probability;
- passive live inference: ML evidence is persisted; Phase 6 can now include it
  in temporal shadow fusion without enabling intervention;
- causal aggregate and per-session ML replay using the Phase 3 metric semantics,
  with a same-test-session rules comparison;
- a dependency-neutral embedding benchmark port for load time, inference
  latency, vector validity/dimension, and peak RSS. No embedding is enabled;
- one command that trains all six inactive candidates and writes an immutable
  comparison report without selecting or activating a winner.

## Developer workflow

Install the optional ML dependencies:

```bash
.venv/bin/python -m pip install -e '.[dev,ml]'
```

Collect and review multiple independent sessions, export one immutable dataset,
then train against its manifest:

```bash
.venv/bin/ad-detect dataset-export --output var/datasets \
  --session recording-001 --session recording-002 --session recording-003

.venv/bin/ad-detect ml-train var/datasets/DIGEST.json \
  --output models/candidates \
  --split-seed ad-detect-split-v1
```

At least three independent recording groups are required, and the training
split must contain `program`, `commercial`, and `unknown` examples. The report
records the exact split, all candidate metrics, per-test-session causal metrics,
the rules comparison, bundle digests, and `accuracy_claim: null`.

To shadow a specifically reviewed local bundle, set `ml.enabled = true` and
`ml.active_model_path` in a local configuration. This only adds persisted ML
evidence; it does not enter the decision path or enable automatic intervention.

## Safety and artifact trust

Timeout, failure, incompatible schema, and unhealthy-capture paths fail closed
to invalid `UNKNOWN`. Model loading uses joblib and therefore must be limited to
locally generated, reviewed bundles. The bundle hash detects modification but
does not make an untrusted serialized object safe.

No candidate is the default. Model registry activation and rollback are Phase 7
work, while combining rules, fingerprints, and ML belongs to Phase 6.

## Verification performed

```text
ruff check: passed
mypy --strict: 57 source files passed
pytest: 88 passed
future-window changes cannot alter an earlier aggregate: tested
label overlap and deterministic disjoint group splitting: tested
all six modality/algorithm paths and three-class probabilities: tested
held-out-session calibration and held-out evaluation: tested
bundle round trip, schema rejection, and artifact tamper rejection: tested
capture/schema hard gates and inference deadline -> UNKNOWN: tested
passive live ML evidence remained isolated before Phase 6 fusion: tested
embedding adapter latency/resource benchmark contract: tested
```

## Acceptance still pending

1. Build a representative three-way labeled corpus spanning channels, genres,
   ad breaks, promos, bumpers, silence, sports, news, and capture faults across
   enough independent sessions for stable grouped splits.
2. Declare the primary selection objective and safety constraints before
   inspecting test results; keep the test recording groups untouched until the
   candidate and thresholds are frozen.
3. Compare every audio, video, and multimodal candidate with rules on unseen
   sessions, including confusion matrices, commercial coverage, false-muted
   program seconds per hour, latency, missed spans, and interval-level review.
4. Plot and inspect per-class reliability, calibration error, Brier score, and
   log loss. Recalibrate or reject overconfident candidates rather than relying
   on accuracy alone.
5. Benchmark aggregation plus inference p50/p95/p99, CPU, RSS, thermal behavior,
   and queue drops during a sustained passive shadow run on the target 2015
   Intel MacBook Pro.
6. Exercise malformed, corrupted, wrong-version, slow, and failing bundles in a
   long-running shadow soak; confirm timeouts do not create capture backlogs.
7. Benchmark any concrete optional audio/video embedding adapter on a clean
   Monterey-compatible install. Enable it only if held-out improvement exceeds
   its measured latency, memory, packaging, and maintenance cost.
8. Select no model if the unseen-session objective is not materially better
   than rules. If one is selected, register it through the later audited Phase
   7 activation/rollback workflow before Phase 6 considers fusion.
