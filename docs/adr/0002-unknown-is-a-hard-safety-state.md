# ADR 0002: UNKNOWN is a hard safety state

Status: accepted, 2026-11-08.

## Context

Black frames, missing audio, HDCP restrictions, capture stalls, excessive A/V
skew, and stale inference contain no positive evidence that the current content
is an advertisement. Treating missing observations as commercial would optimize
coverage by muting programming and failure states.

## Decision

Represent `PROGRAM`, `COMMERCIAL`, and `UNKNOWN` in every detector and temporal
contract. Invalid detector evidence is prohibited from assigning positive
commercial probability. Runtime health failure forces `UNKNOWN`; the decision
policy inhibits mute, ends the break lease, and requests restoration if the
application owns an active mute.

## Consequences

The application may leave commercial audio audible during capture failures. This
is the intended safe failure direction. Unknown coverage and reason codes are
reported separately so reliability can improve without weakening the invariant.

