# ADR 0005: Append-only labels and content-addressed datasets

Status: accepted on 2026-11-08.

## Context

Training and evaluation must remain reproducible after a reviewer corrects a
label. In-place edits would erase provenance, while mutable export files could
silently change the evidence behind a metric or model. Retention must also avoid
deleting media that has become labeled.

## Decision

- Every label is an immutable revision with author, creation time, confidence,
  optional boundary kind, and an optional `supersedes_id`.
- A correction appends one revision and never updates or deletes its parent.
- Only the unsuperseded tip of each revision chain is effective for replay.
- Dataset exports include the complete revision history, versioned feature
  windows, explicit missingness, session IDs, and declared provenance.
- Canonical JSON is named by its SHA-256 digest and verified before import.
- Imports preserve revision IDs and reconstruct parents before children.
- Retained artifacts have one or more session-time spans. Adding an overlapping
  label permanently protects the artifact from quota deletion.

## Consequences

Corrections remain auditable and exports are reproducible for a fixed timestamp
and provenance map. Storage may remain above quota when protected evidence alone
exceeds the limit; that is an intentional safety result and must be surfaced to
the operator. Dataset manifests contain metadata and features, while media stays
in the separate local artifact store unless an explicit packaging workflow is
added later.

The schema moves to version 3. Versions 1 and 2 migrate forward without deleting
events or artifacts.

## Rollback conditions

Replace this representation only if a future labeling backend provides the same
immutable revision history, deterministic export, referential validation, and
label-aware retention guarantees.
