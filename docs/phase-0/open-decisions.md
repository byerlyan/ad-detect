# Open architectural decisions

These questions are unresolved because Phase 0 lacks the target hardware
measurements or labeled recordings needed for an honest answer. Defaults below
let implementation proceed without pretending they are final.

| ID | Decision | Provisional default | Evidence needed | Resolve by |
| --- | --- | --- | --- | --- |
| O1 | Does the MS2130 expose muxed A/V or separate AVFoundation endpoints, and what modes are stable? | One FFmpeg AVFoundation input selecting video + audio | Device enumeration, accepted-format probe, 2 h PTS/drift capture | Phase 2 start |
| O2 | 720p vs 1080p capture and analysis cadence | Capture accepted native mode; analyze downscaled at 2 fps | CPU/quality benchmark and small-text/logo visibility | Phase 2 |
| O3 | Can Apple TV safely report and set the actual downstream volume for this TV/soundbar path? | Automatic mute unavailable until proven | pyatv feature report plus change/restore test across apps and HDMI-CEC setup | Phase 2 |
| O4 | Which pyatv release is reliable on current tvOS and Python 3.11/Monterey? | pyatv 0.18.0 is software-pinned and mock-tested; keep provisional | Discovery/pair/connect/reconnect/volume soak on the real device | Phase 2 hardware acceptance |
| O5 | FFmpeg distribution/build and licensing | User-installed signed/Homebrew-style binary, version-probed | Clean Monterey installation and license inventory | Phase 1/2 |
| O6 | PyAV container demux vs dedicated raw-pipe parser | Prefer timestamped NUT/Matroska demux with PyAV | Prototype reliability, PTS fidelity, shutdown behavior | Phase 2 spike |
| O7 | Audio landmark sample rate/hash parameters and SQLite scale | 16 kHz mono; SQLite hash index | Positive/negative capture benchmark, query p95, collision/size results | Phase 4 |
| O8 | Logistic regression vs histogram gradient boosting | Train/report both; simplest non-inferior model | Grouped session replay with calibration and resource metrics | Phase 5 |
| O9 | Hysteresis vs HMM in active runtime | HMM only if it improves safety metrics | Paired session replay and failure-case review | Phase 6 |
| O10 | Whether YAMNet/MobileNet embeddings justify old runtime pins | Disabled by default | Clean-install benchmark plus held-out ablation gain | Phase 5 |
| O11 | Web frontend component/build choice | Small TypeScript bundle, no SSR | Setup/timeline prototype complexity and accessibility tests | Phase 8 |
| O12 | LAN authentication model | Generated local admin password + secure cookie/CSRF | Threat review and phone setup usability test | Phase 8/9 |
| O13 | Default ring/retention duration and encoding | 60 s pre/30 s post in RAM; media persistence opt-in | RAM/disk benchmark and user preference | Phase 3 |
| O14 | Required false-muted safety gate | Optimize first; do not set a fictional number | Representative labeled sessions and user tolerance | Before auto-mute beta |
| O15 | Exact Mac CPU/RAM/storage and sustained thermal behavior | Assume lowest practical budget | Hardware inventory and 2 h soak | Before Phase 2 acceptance |
| O16 | Project distribution license | Keep package metadata explicitly provisional; do not copy reference code | Project-owner selection and dependency/model license review | Before first distribution |

## Decision protocol

Each resolution becomes an ADR containing context, measured alternatives,
decision, consequences, dependency/model versions, and rollback conditions.
Benchmark artifacts and dataset manifests are linked rather than summarized as
unsupported claims.

