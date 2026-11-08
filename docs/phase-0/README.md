# Phase 0 index

Status: proposed architecture, researched 2026-11-08. Decisions marked
"provisional" require measurement on the actual capture chain before Phase 2
can be accepted.

| Deliverable | Document |
| --- | --- |
| Repository structure and system architecture | [architecture.md](architecture.md) |
| Typed component interfaces | [interfaces.md](interfaces.md) |
| ML comparison, capture, fingerprinting, temporal strategy, technology decisions | [research-and-decisions.md](research-and-decisions.md) |
| Dataset, training, and evaluation | [research-and-decisions.md](research-and-decisions.md#dataset-and-labeling-design) |
| Apple TV control architecture | [architecture.md](architecture.md#apple-tv-control) |
| Performance targets | [performance-budget.md](performance-budget.md) |
| Risks and mitigations | [risk-register.md](risk-register.md) |
| Phases, dependencies, and acceptance criteria | [roadmap.md](roadmap.md) |
| Unresolved architectural decisions | [open-decisions.md](open-decisions.md) |

## Decision summary

1. One modular Python process, with isolated worker threads/processes only where
   profiling justifies them; no service mesh or broker.
2. One FFmpeg/AVFoundation owner captures timestamped audio and video and fans
   out immutable packets through bounded queues.
3. Missing, black, frozen, or desynchronized capture maps to `UNKNOWN` and
   inhibits automatic mute.
4. The first trainable model is a calibrated scikit-learn classifier over
   inexpensive aggregated audio/video features. Optional pretrained embeddings
   must pass a hardware benchmark before entering the default runtime.
5. Known ads use a local landmark-hash audio fingerprint index. Matches are
   evidence, not unconditional commands.
6. A three-state temporal fusion layer and a separate conservative decision
   policy govern intervention.
7. Apple TV actions are capability-driven, idempotent, supervised by a watchdog,
   and restore only a volume state the application actually observed.

