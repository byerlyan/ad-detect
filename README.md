# Ad Detect

Ad Detect is a planned local-first application for real-time television
advertisement detection and safe playback automation. It is designed for an
Intel Mac running macOS Monterey, an HDMI capture device, and an Apple TV, with
portable boundaries for future Linux support.

The project has completed its Phase 0 architecture package and has a
**Phase 1 executable core**. It includes validated configuration, immutable
domain contracts, bounded fan-out, deterministic simulated capture, temporal
hysteresis, a safety-oriented decision policy, idempotent simulated playback
control, SQLite events, a FastAPI health/state API, and a responsive dashboard
shell. No real hardware compatibility or detection accuracy is claimed yet.
Phase 2 software adapters for FFmpeg/AVFoundation and pyatv are implemented and
mock-tested, but remain hardware-unvalidated. Phase 3 now adds versioned
classical A/V features, an explainable rules baseline, causal replay metrics,
append-only labels, label-safe artifact retention, and immutable dataset
export/import. A live FFmpeg-to-rules shadow runtime is wired and mock-tested;
it is deliberately unable to control Apple TV playback. Real capture hardware
and detection accuracy remain unvalidated.

## Development quick start

Python 3.11 is required.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/ad-detect simulate
.venv/bin/ad-detect serve
# After selecting and probing real capture devices:
.venv/bin/ad-detect capture-shadow --session recording-001
```

The local dashboard defaults to <http://127.0.0.1:8765>. The default operating
mode is `shadow`; the simulated pipeline never controls external hardware.

Runtime state is written under `var/` and is excluded from source control.

## Phase 0 documents

- [Architecture](docs/phase-0/architecture.md)
- [Component contracts](docs/phase-0/interfaces.md)
- [ML and technology research](docs/phase-0/research-and-decisions.md)
- [Performance budget](docs/phase-0/performance-budget.md)
- [Risk register](docs/phase-0/risk-register.md)
- [Implementation roadmap](docs/phase-0/roadmap.md)
- [Open decisions](docs/phase-0/open-decisions.md)
- [Phase 1 implementation status](docs/phase-1/README.md)
- [Phase 2 integration status](docs/phase-2/README.md)
- [Phase 3 rule/data status](docs/phase-3/README.md)

## Phase 1 source map

- `src/ad_detect/domain/`: immutable three-state records and invariants
- `src/ad_detect/capture/`: deterministic source and bounded fan-out
- `src/ad_detect/temporal/`: directional hysteresis baseline
- `src/ad_detect/policy/`: expiring, restoration-first decisions
- `src/ad_detect/intervention/`: idempotent simulated actuator
- `src/ad_detect/data/`: SQLite migrations and canonical event store
- `src/ad_detect/features/`: causal windows and classical A/V features
- `src/ad_detect/detectors/`: explainable rules evidence
- `src/ad_detect/learning/`: immutable datasets and causal replay metrics
- `src/ad_detect/app/`: causal tick orchestration
- `src/ad_detect/api/`: FastAPI lifecycle, read-only API, dashboard shell
- `tests/`: unit, integration, and deterministic replay coverage

The design is independent from
[The Clicker](https://github.com/michigandrew/the-clicker). That MIT-licensed
project was reviewed for product and integration lessons; no source has been
copied into this repository.

