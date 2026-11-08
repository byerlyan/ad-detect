# Phase 9 mobile dashboard and secure LAN plan

Status: planned. A loopback FastAPI status shell and read-only APIs exist; the
authenticated LAN session layer, mutation APIs, responsive application, and
browser automation suite do not.

## Objective

Provide a trustworthy couch-distance interface for live status, review,
labeling, manual safety control, and approved model/config operations. A stale
or disconnected page must be visibly stale, and no browser mutation may bypass
authentication, authorization, CSRF/origin checks, or audit logging.

## Entry criteria

- Phase 6 runtime exposes classification, confidence, contributions, break,
  override, mute lease, watchdog, and connection state.
- Phase 7 registry/evaluation services expose read models and authorized
  activation/rollback contracts.
- Phase 8 setup and configuration services are transactional and resumable.
- O11 frontend choice and O12 LAN authentication model are resolved in ADRs.
- Loopback-only mode remains fully usable when LAN mode is disabled.

## Product areas

### Live dashboard

- capture/audio/video health and freshness;
- Apple TV connection/capability state;
- PROGRAM, COMMERCIAL, or UNKNOWN with confidence and age;
- rules, fingerprint, ML, and temporal contributions with explanations;
- actual and recommended audio state, break state, lease, watchdog, and mode;
- recent decisions, interventions, gaps, drops, and failures;
- prominent disconnected/stale banner based on server timestamps and sequence.

### Activity and review

- touch-friendly commercial-break and health timeline;
- filters for false-muted program, missed ad, `UNKNOWN`, capture gap, and model;
- one-tap commercial/program/unknown labels with boundary adjustment;
- correction history rather than in-place label edits;
- retained event preview subject to retention and privacy settings;
- reviewed fingerprint enrollment with duplicate/quality warnings.

### Manual controls

- immediate program override/restore as the primary safety control;
- manual mute only when intervention capabilities and current authorization
  permit it;
- start/pause detection and shadow controls;
- quick sensitivity profile with a preview of changed settings;
- explicit state and acknowledgement after every command.

Queued commands carry generations. Enabling override or requesting restore must
supersede older mute work and cannot be immediately reversed by a stale request.

### Model and evaluation views

- active and previous registered model, digest, compatibility, and provenance;
- training dataset counts and grouped-session coverage;
- evaluation history, aggregate and per-session metrics, calibration, and
  resource results;
- explicit activation and rollback confirmation with reason and audit actor;
- no upload or loading of arbitrary browser-supplied serialized model files.

### Settings and mobile calibration

Expose the Phase 8 Basic/Advanced settings services with mobile layouts,
validation diffs, dependency invalidations, and restart requirements. Reuse the
same capture/calibration jobs; do not implement separate mobile-only state.

## Live transport and freshness

Use one ordered server event stream, preferably SSE unless bidirectional needs
justify WebSocket. Each update carries session ID, monotonically increasing
sequence, generated timestamp, source timestamp, and schema version. The client
detects gaps, reconnects with a cursor where supported, and refreshes a complete
snapshot before resuming live presentation.

The page marks state stale after a short configured deadline and disconnected
after transport loss. It never leaves an old green/live indicator on screen.
State mutations use normal HTTP requests with idempotency keys and do not rely
on the live stream for authorization.

## LAN security model

- bind to loopback by default; LAN listening requires explicit setup;
- require an authenticated local-admin session for every mutation and sensitive
  read;
- store only a modern salted password verifier, never the password;
- use server-side revocable sessions with short idle and absolute lifetimes;
- set `HttpOnly` and `SameSite=Strict` cookies; use `Secure` when served over
  HTTPS and do not describe plain HTTP as secure remote access;
- require CSRF tokens plus strict `Origin`/`Host` allowlists for mutations;
- rate-limit login, pairing, intervention, activation, and label endpoints;
- rotate bootstrap/admin secrets and invalidate sessions after security changes;
- never expose pairing credentials, model serialization, filesystem paths, raw
  exception details, password material, or diagnostic secrets;
- provide no port forwarding, cloud relay, UPnP, or public-internet mode.

Document the supported HTTPS or trusted local reverse-proxy path before calling
LAN mode secure. Complete a threat review covering malicious sites on the same
browser, another LAN client, stolen cookies, replayed commands, DNS rebinding,
and compromised diagnostic exports.

## API mutation rules

All mutation endpoints require authentication, authorization, CSRF/origin
validation, content-type validation, bounded payloads, idempotency keys where
appropriate, and append-only audit events. Return conflict responses for stale
configuration revisions or command generations rather than last-write-wins.

High-risk mutations—automatic consent, model activation/rollback, credential
rotation, retention expansion, and manual mute—require a recent authenticated
session and a confirmation reason. Restore and program override remain easy to
reach but still authenticated and audited.

## Accessibility and responsive behavior

- minimum 44 by 44 CSS-pixel touch targets for primary controls;
- complete keyboard operation and visible focus;
- semantic landmarks, labels, status announcements, and error association;
- color-independent PROGRAM/COMMERCIAL/UNKNOWN and connection states;
- reduced-motion support and sufficient contrast in light/dark themes;
- layouts tested at representative narrow phone, tablet, laptop, and desktop
  viewports without horizontal control overflow.

## Optional PWA boundary

The installable shell is optional. If enabled, cache only immutable static
assets. Never cache API responses, credentials, live state, labels, reports with
sensitive data, or mutation requests. An offline shell must clearly say it is
offline and disable all controls; it must not display cached state as current.

## Verification plan

- backend authorization matrix for anonymous, expired, wrong-role, stale-CSRF,
  wrong-origin, replayed-idempotency, and valid requests;
- browser tests for login/logout, session expiry, credential rotation, CSRF,
  reconnect, event gaps, stale state, and concurrent configuration conflicts;
- Playwright coverage for phone and desktop live status, timeline, labeling,
  override/restore, settings, model activation, and rollback;
- automated accessibility checks plus keyboard and screen-reader manual review;
- secret-scanning response tests for every endpoint and diagnostics download;
- load tests for event fan-out and slow/disconnected clients without blocking
  capture, persistence, policy, or intervention;
- security review for LAN binding, cookies, headers, origin/host validation,
  rate limits, and dependency exposure.

## Exit criteria

- Representative phone and desktop flows pass browser and accessibility tests.
- Stale, disconnected, gap-recovery, and server-restart states are unmistakable.
- Every state change is authenticated, authorized, CSRF/origin protected,
  idempotent where necessary, and audited.
- Pairing credentials and other secrets never reach the browser.
- Manual program override/restore supersedes stale mute commands.
- LAN deployment documentation describes the threat model, supported secure
  transport, password/session recovery, and prohibition on public exposure.

## Acceptance still pending

1. Resolve O12 through a threat review and phone setup usability test.
2. Test real iOS Safari and at least one other current mobile browser on the
   intended LAN topology.
3. Validate long-lived event reconnect across phone sleep/wake and Wi-Fi change.
4. Complete a user review of touch safety, stale-state comprehension, timeline
   labeling, and activation confirmations.

## Explicit non-goals

- no public cloud access, remote relay, or internet-facing deployment;
- no unauthenticated convenience mutations;
- no browser access to pairing credential files or joblib artifacts;
- no offline control or cached live-state representation.
