# ADR 0007: Session-grouped tabular ML bundles

Status: accepted as a provisional Phase 5 baseline on 2026-11-08.

## Context

The rules baseline needs a measured ML comparison without leaking neighboring
windows or rebroadcast copies across training and evaluation. Runtime loading
must bind preprocessing to the model, reject incompatible feature contracts,
and keep optional inference from delaying capture or silently enabling mute.
The target Intel Mac favors small CPU models over an assumed deep-learning
stack.

## Decision

- Aggregate classical features causally over 1, 3, 10, and 30 second windows,
  including distribution, trend, transition, coverage, and missingness values.
- Split complete recording groups into disjoint training, calibration, and test
  sets. Known rebroadcast variants may share a group and therefore a split.
- Compare audio-only, video-only, and early-fusion candidates using logistic
  regression and histogram gradient boosting.
- Fit preprocessing and the classifier only on training sessions. Fit optional
  multinomial probability calibration only on separate calibration sessions.
- Save preprocessing, classifier, calibrator, ordered columns/classes, schemas,
  dependency versions, dataset digest, and held-out metric summary as one
  content-addressed bundle.
- Verify the manifest path, artifact digest, schema, columns, classes, and exact
  scikit-learn and NumPy versions before deserializing a locally trusted bundle.
- Bound live inference by a configured deadline. Invalid capture, incompatible
  schemas, timeouts, and failures produce invalid `UNKNOWN` evidence with zero
  commercial probability.
- Persist live ML evidence passively. It cannot affect the rules-only decision
  path until Phase 6 evaluates fusion through identical causal replay.
- Training writes all six inactive candidates and a rules comparison report;
  it never selects or activates a model.

## Consequences

The baseline is small, reproducible for an immutable dataset and environment,
and explicitly testable for leakage and future-window access. Exact dependency
matching is conservative but avoids pretending joblib compatibility across
versions. Joblib remains executable serialization: content hashes provide
integrity, not trust, so externally supplied bundles must not be loaded.

The six algorithms and modalities are candidates, not a product claim.
Representative recordings, per-session error review, probability reliability,
and target-hardware latency/resource measurements are required before one may
be selected. Optional embedding adapters remain benchmark-only until they show
held-out gain within the resource budget.

## Rollback conditions

Retain the rules baseline if no candidate improves the declared unseen-session
objective or if calibration and resource gates fail. Replace the serializer or
model family only with a new bundle format and a paired causal comparison
report; never silently reinterpret an existing bundle.
