# ADR 0001: Start as a modular monolith

Status: accepted, 2026-11-08.

## Context

The application owns one local capture chain, must make low-latency decisions,
and must remain installable on an older Intel Mac. Distributed services and a
message broker would add failure modes without solving a present scaling need.

## Decision

Use one Python application with dependency-free domain records and explicit
ports around capture, detectors, persistence, and intervention. Use bounded
in-process channels. CPU-heavy work may move to a supervised worker process
behind the same port only after profiling demonstrates the need.

## Consequences

Deployment and failure recovery remain understandable. Component boundaries are
still independently testable. A detector crash must be contained at its adapter
boundary; the modular-monolith decision does not permit shared hidden state.

Revisit if measured isolation or CPU scheduling requirements cannot be met in
one supervised application.

