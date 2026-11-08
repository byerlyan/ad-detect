# Ad Detect

Ad Detect is a planned local-first application for real-time television
advertisement detection and safe playback automation. It is designed for an
Intel Mac running macOS Monterey, an HDMI capture device, and an Apple TV, with
portable boundaries for future Linux support.

The project has completed its architecture package, has an executable software
baseline through **Phase 6**, and now includes the Phase 7 evaluation/registry
foundation. It includes validated configuration, immutable domain contracts,
bounded fan-out, deterministic simulated capture, temporal
hysteresis, a safety-oriented decision policy, idempotent simulated playback
control, SQLite events, a FastAPI health/state API, and a responsive dashboard
shell. No real hardware compatibility or detection accuracy is claimed yet.
Phase 2 software adapters for FFmpeg/AVFoundation and pyatv are implemented and
mock-tested, but remain hardware-unvalidated. Phase 3 now adds versioned
classical A/V features, an explainable rules baseline, causal replay metrics,
append-only labels, label-safe artifact retention, and immutable dataset
export/import. A live FFmpeg-to-fusion shadow runtime is wired and mock-tested;
it is deliberately unable to control Apple TV playback. Real capture hardware
and detection accuracy remain unvalidated.
Phase 4 adds local landmark audio fingerprints, trusted-label enrollment,
offset-aligned subclip matching, continuity guards, passive live scanning, and
a fingerprint-only replay baseline. Its thresholds remain provisional pending
real captured commercials and negative program sessions.
Phase 5 adds causal multi-window aggregation, leakage-safe recording-group
splits, six tabular ML candidates, held-out calibration, immutable model
bundles, causal comparison reports, and deadline-bounded passive live
inference. No model is selected or activated automatically, and no accuracy
claim is made without representative session-held-out data.
Phase 6 adds deadline-aware evidence fusion, comparable hysteresis,
sliding-window, and three-state HMM classifiers, an independent conservative
decision policy, immediate manual-program override, and a mute-lease watchdog.
Hysteresis remains the default until unseen-session replay justifies a change;
live capture remains hard-wired to shadow intervention.
Phase 7 now provides content-addressed evaluation runs, immutable final-test
partitions, baseline JSON/HTML reports, and an audited model registry whose
registration path never activates a model. Full representative evaluation and
authenticated operator activation remain pending.

## Development quick start

Python 3.11 is required; Python 3.9, 3.10, and 3.12+ are intentionally rejected
by the package metadata. A virtual environment keeps the interpreter it was
created with, so activating an old `.venv` does not upgrade it to Python 3.11.

### 1. Confirm the host tools

On macOS, verify the interpreter and external FFmpeg executable before creating
the environment:

```bash
python3.11 --version       # must print Python 3.11.x
command -v python3.11
ffmpeg -version | head -1
```

If `python3.11` or FFmpeg is missing and Homebrew is available:

```bash
brew install python@3.11 ffmpeg
```

### 2. Create the environment

The preferred setup uses the checked-in `uv.lock`:

```bash
uv sync --python 3.11 --extra dev
.venv/bin/python --version
```

The second command must report Python 3.11.x. `uv sync` installs the project and
development tools into `.venv` using the locked dependency graph.

Alternatively, use standard `venv` and pip:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Do not create the environment with an unversioned `python` or `python3` command
unless `--version` has already confirmed it is Python 3.11.

### Repair an environment created with the wrong Python

This error means the active environment was created with an unsupported
interpreter:

```text
Package 'ad-detect' requires a different Python: 3.9.6 not in '<3.12,>=3.11'
```

Virtual environments cannot change their base interpreter in place. Leave the
old environment, move it aside so it remains recoverable, and recreate it:

```bash
deactivate 2>/dev/null || true
mv .venv .venv-python39-backup
python3.11 -m venv .venv
.venv/bin/python --version
.venv/bin/python -m pip install -e '.[dev]'
```

After confirming the new environment works, the backup can be removed manually.
If the shell still resolves an old interpreter, compare these paths:

```bash
which python
python --version
.venv/bin/python --version
```

The `(.venv)` prompt only shows that some environment is active; it does not
prove that environment uses the required Python version.

### 3. Verify the development installation

Run the same consolidated checks used during development:

```bash
./scripts/check.sh
```

Or run the individual smoke tests:

```bash
.venv/bin/python -m pytest
.venv/bin/ad-detect --help
.venv/bin/ad-detect simulate
```

### 4. Run the local application

```bash
.venv/bin/ad-detect serve
```

The dashboard defaults to <http://127.0.0.1:8765>. The default operating mode is
`shadow`; the simulated pipeline never controls external hardware.

Optional dependency groups can be installed as needed:

```bash
# Capture decoding
.venv/bin/python -m pip install -e '.[dev,capture]'

# Apple TV integration
.venv/bin/python -m pip install -e '.[dev,appletv]'

# ML/OpenCV experiments
.venv/bin/python -m pip install -e '.[dev,ml]'

# All current optional integrations
.venv/bin/python -m pip install -e '.[dev,capture,appletv,ml]'
```

For real capture, select exact device names in `configs/defaults.toml`, then run:

```bash
.venv/bin/ad-detect capture-devices
.venv/bin/ad-detect capture-probe
.venv/bin/ad-detect capture-shadow --session recording-001
```

Runtime state is written under `var/` and is excluded from source control.

To train the inactive Phase 5 candidates after collecting and reviewing at
least three independent labeled sessions:

```bash
.venv/bin/ad-detect dataset-export --output var/datasets \
  --session recording-001 --session recording-002 --session recording-003
.venv/bin/ad-detect ml-train var/datasets/DIGEST.json \
  --output models/candidates

# Compare all temporal strategies on the same causal session replay.
.venv/bin/ad-detect temporal-replay --session recording-001

# Create an immutable held-out report, then register a reviewed bundle inactive.
.venv/bin/ad-detect evaluation-run var/datasets/DIGEST.json \
  --candidate models/candidates/BUNDLE_DIGEST
.venv/bin/ad-detect model-register models/candidates/BUNDLE_DIGEST \
  --evaluation EVALUATION_DIGEST --actor local-reviewer
.venv/bin/ad-detect model-list
```

Training writes six content-addressed bundles and one comparison report. It
does not modify configuration or activate a model.

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
- [Phase 4 fingerprinting status](docs/phase-4/README.md)
- [Phase 5 ML inference status](docs/phase-5/README.md)
- [Phase 6 temporal fusion and policy status](docs/phase-6/README.md)
- [Phase 7 training, evaluation, and registry plan](docs/phase-7/README.md)
- [Phase 8 guided setup and configuration plan](docs/phase-8/README.md)
- [Phase 9 mobile dashboard and secure LAN plan](docs/phase-9/README.md)
- [Phase 10 reliability and release plan](docs/phase-10/README.md)

## Source map

- `src/ad_detect/domain/`: immutable three-state records and invariants
- `src/ad_detect/capture/`: deterministic source and bounded fan-out
- `src/ad_detect/temporal/`: deadline-aware hysteresis, sliding-window, and HMM fusion
- `src/ad_detect/policy/`: expiring, restoration-first decisions
- `src/ad_detect/intervention/`: idempotent simulated actuator
- `src/ad_detect/data/`: SQLite migrations and canonical event store
- `src/ad_detect/features/`: causal windows and classical A/V features
- `src/ad_detect/detectors/`: rules, fingerprints, and bounded ML evidence
- `src/ad_detect/learning/`: datasets, aggregation, candidate training, and replay
- `src/ad_detect/fingerprint/`: landmark extraction, matching, and query windows
- `src/ad_detect/app/`: causal tick orchestration
- `src/ad_detect/api/`: FastAPI lifecycle, read-only API, dashboard shell
- `tests/`: unit, integration, and deterministic replay coverage

The design is independent from
[The Clicker](https://github.com/michigandrew/the-clicker). That MIT-licensed
project was reviewed for product and integration lessons; no source has been
copied into this repository.

