# Risk register

Probability and impact are qualitative until hardware and session evidence are
available. Owners name the component responsible for mitigation, not a person.

| ID | Risk | Probability | Impact | Mitigation / acceptance evidence | Owner |
| --- | --- | --- | --- | --- | --- |
| R1 | HDCP or splitter behavior produces black/unavailable content | High | High | Health detector maps black/frozen/dropout to `UNKNOWN`; setup probe explains limitation; no bypass. Test each source/app. | Capture |
| R2 | MS2130 exposes audio/video as separate clocks or no audio | Medium | High | Enumerate/probe modes, preserve PTS, measure drift; permit video-only detection but block claims of synchronized multimodal input. | Capture |
| R3 | Device names/indices change across reboot/USB ports | High | Medium | Store verified signature plus name; re-probe at start; wizard resolves ambiguity, never select silently. | Setup/Capture |
| R4 | FFmpeg/AVFoundation permissions or format negotiation fails on Monterey | Medium | High | Guided permission test, enumerate accepted modes, stderr diagnostics, clean-host install acceptance. | Capture/Installer |
| R5 | 2015 CPU cannot sustain optional embeddings | High | Medium | Classical core, benchmark gate, adaptive cadence, bounded queues, automatic optional-detector disable. | Inference |
| R6 | Pinned Intel macOS ML wheels age out or conflict | High | Medium | Optional dependency group, hash/lock artifacts, model-independent ports, no ORT/TensorFlow in core. | Packaging |
| R7 | Training data is too small, channel-specific, or class-imbalanced | High | High | Shadow collection, session/source reports, grouped splits, conservative baseline, no auto-promotion. | Learning |
| R8 | Temporal leakage inflates evaluation | High without controls | High | Immutable manifests; recording/session/rebroadcast grouping; final untouched test set; causal replay. | Evaluation |
| R9 | Fingerprints false-match common bumpers, music, or silence | Medium | High | Landmark density, aligned count/ratio, runner-up margin, continuity, ambiguity/denylist, non-match threshold set. | Fingerprint |
| R10 | Edited/time-scaled commercials evade fingerprints | Medium | Medium | Fingerprint is one detector; tune landmark tolerances; measure variants; classifier remains fallback. | Fingerprint/ML |
| R11 | Program promos resemble ads and cause false muting | High | High | Treat objective as safety-asymmetric; diverse labels; multi-family entry evidence; fast recovery; manual correction. | Temporal/Policy |
| R12 | News/sports rapid cuts and loud segments mimic ads | High | High | Session/source-held-out testing, logo as supporting program evidence, calibrated temporal context. | ML/Policy |
| R13 | Apple TV absolute volume is unavailable or controls a different endpoint | High | High | Runtime feature check; hardware calibration; step-only mode explicit opt-in; otherwise shadow/detection only. | Intervention |
| R14 | Step restoration changes a user's chosen volume | Medium | High | Prefer observe/set/restore; lease ownership; restore no more than issued steps; manual volume change cancels restore where observable. | Intervention |
| R15 | Connection loss or crash leaves audio muted | Medium | Critical | Short renewable mute lease, max mute timer, signal/atexit restore attempt, launch-time reconciliation, prominent alert; verify failure injection. | Intervention/Policy |
| R16 | Repeated commands cause volume oscillation | Medium | High | Conflated desired state, action generations, acknowledgements, serialized idempotent supervisor. | Intervention |
| R17 | Laptop sleep/thermal throttling interrupts automation | Medium | High | Power/thermal diagnostics, stale-data restoration, explicit sleep handling, soak test. | Runtime |
| R18 | Disk retention captures sensitive content or fills disk | Medium | High | Default minimal/off media, quotas, previews, content hashes, labeled-data protection, retention reports. | Data |
| R19 | LAN dashboard enables unauthorized playback control | Medium | High | Loopback default, explicit LAN enable, authentication, CSRF/origin checks, secret rotation, no public exposure guidance. | API/Security |
| R20 | Pairing credentials leak through API/log/backup | Low | Critical | User-only file permissions, redaction, API schema exclusion, security tests, document backup implications. | Intervention/Security |
| R21 | SQLite contention blocks capture/control | Low | High | WAL, short async repository operations, bounded persistence spool, no media blobs, overload test. | Data |
| R22 | `UNKNOWN` dominates and makes system ineffective | Medium | Medium | Reason-coded unknowns, readiness dashboard, health calibration, measure unknown coverage separately. Never weaken safety by treating unknown as ad. | Product/Capture |
| R23 | Framework or model license is incompatible | Low | High | Software/model inventory with SPDX/source/hash; review before adding weights; record FFmpeg build license. | Packaging |
| R24 | Reference-project patterns accidentally become copied implementation | Low | Medium | Independent package/contracts, retain attribution in research, no source copied; review provenance. | Architecture |

## Critical failure invariants

The following are non-negotiable acceptance conditions:

- Missing or unhealthy capture cannot initiate or renew mute.
- A stale or expired decision cannot initiate or renew mute.
- Maximum mute duration is enforced independently of detector progress.
- Manual restore/override cannot be immediately reversed by queued old work.
- Automatic retention cannot delete user-labeled data.
- A newly trained model cannot activate without an explicit audited action.

