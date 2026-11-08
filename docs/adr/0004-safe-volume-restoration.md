# ADR 0004: Automatic mute requires observable absolute volume

Status: provisionally accepted, 2026-11-08; real audio-path test required.

## Context

Volume-up/down steps are device-dependent and may not be linear. Fixed down/up
counts can restore the wrong volume, fight a user's manual change, or leave the
system silent after failure.

## Decision

Automatic Apple TV muting is eligible only when pyatv reports both current-volume
observation and absolute-volume setting as available. Immediately before mute,
the adapter records the observed volume. It restores that value only while it
still owns the mute lease and the current volume remains muted. A nonzero manual
change relinquishes ownership and is never overwritten.

Step capabilities are reported for diagnostics but are not a safe automatic
fallback. Unsupported configurations use detection or shadow mode.

## Consequences

Some otherwise controllable Apple TV/audio configurations will not support
automatic mute. This is preferable to unsafe restoration. The decision remains
provisional until tested against the actual Apple TV, television, soundbar, and
HDMI-CEC path.

