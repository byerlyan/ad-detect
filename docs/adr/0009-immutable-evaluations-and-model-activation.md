# ADR 0009: Immutable evaluations and explicit model activation

Status: accepted as a provisional Phase 7 baseline on 2026-11-08.

## Context

Training, evaluation, registration, and live activation have different trust
boundaries. Recomputing a split or report in place can silently change the
evidence behind a claim. Treating a trained artifact as active would allow
unreviewed data or code to alter live behavior. Joblib artifacts also require a
local trust decision in addition to content integrity.

## Decision

- Name evaluation directories by a canonical manifest digest and never
  overwrite an earlier logical run.
- Bind the dataset, config, report, model bundles, grouped split, source revision
  and dirty state, schemas, command, host profile, and timestamps in the run
  manifest.
- Lock the final-test session partition by dataset digest. A later attempt to
  change the partition or grouping definition fails instead of silently
  producing a more favorable test.
- Emit canonical JSON plus an escaped, offline HTML representation. Keep
  `accuracy_claim` null until representative held-out and hardware gates pass.
- Store evaluation metadata, model registration, the active pointer, and every
  activation outcome in SQLite schema v5. Keep large reports and bundles in
  content-addressed filesystem directories.
- Require a complete evaluation containing the exact model digest before
  registration. Registration is idempotent and never changes the active pointer.
- Require an authorized actor permission, reason, config digest, and successful
  bundle integrity/compatibility reload before activation or rollback.
- Update the active pointer and success audit in one transaction. Record failed
  validation attempts without changing the pointer.
- Do not expose activation through the current unauthenticated API. Phase 9 must
  provide the production authentication, CSRF, session, and audit actor boundary.
- Allow live ML to resolve the registry pointer only through explicit
  `ml.use_registry=true`; direct bundle paths and registry selection are mutually
  exclusive.

## Consequences

Development runs from a dirty worktree remain possible but visibly cannot serve
as clean release evidence. Dataset corrections create a different dataset
digest and therefore a new test lock and evaluation lineage. The same immutable
bundle may be registered idempotently after activation without changing audit
history.

The initial HTML report is intentionally simple and safe. Fingerprint-only and
combined-detector comparisons remain unavailable when the dataset lacks PCM or
aligned detector evidence; the report names these omissions rather than
fabricating a complete matrix.

Content hashes prove integrity, not trust. Registration and activation load only
locally generated, reviewed bundles; browser-supplied serialized models remain
out of scope.

## Rollback conditions

Rollback targets the previous registered model recorded by an activation event,
reloads it under the same compatibility checks, and appends a new rollback
event. If no previous model exists or validation fails, the active pointer is
unchanged. Replace this design only with a system preserving immutable test
assignment, full provenance, inactive registration, explicit authorization,
transactional pointer changes, and append-only audit.
