# Research and technology decisions

Research date: 2026-11-08. Compatibility claims below are based on published
artifacts and must be reproduced on the target Mac before dependency pins are
accepted.

## Recommended stack

| Concern | Initial choice | Reason |
| --- | --- | --- |
| Language | CPython 3.11 | Mature typing/performance; compatible wheels exist for the target-era stack |
| Packaging | `pyproject.toml` + `uv.lock`; separate `core`, `ml-optional`, and `dev` groups | Reproducible core without forcing heavyweight ML |
| API | FastAPI + Uvicorn | Typed schemas, WebSocket/SSE support, compact local deployment |
| UI | TypeScript + lightweight component layer, bundled static assets | Responsive/PWA-capable without a second runtime service |
| Capture | FFmpeg subprocess with AVFoundation; PyAV or a small demux adapter | One A/V owner, negotiated formats, PTS preservation, restart isolation |
| Video | OpenCV headless + NumPy | Efficient classical features and image transforms |
| Audio | NumPy + SciPy; `python-soxr` only if resampling quality/cost warrants | Avoid librosa/numba in the always-on runtime |
| Baseline ML | scikit-learn | Strong tabular models, calibration, portable CPU inference |
| Optional inference | `onnxruntime==1.19.2`, isolated extra | Its CPython 3.11 universal2 wheel supports macOS 11+; later versions must not silently replace it |
| Metadata | SQLite in WAL mode + migrations | Local, transactional, inspectable, no daemon |
| Artifacts | Content-addressed local files with SQLite manifests | Efficient clips/arrays and reliable provenance |
| Apple TV | pyatv behind an adapter | Discovery, pairing, feature inspection, volume API, connection callbacks |
| Tests | pytest; Playwright for browser flows | Hardware ports can be simulated; mobile viewport regression coverage |

FFmpeg is a separately installed executable and must be version-probed. Its
license/build configuration is recorded because codec options affect licensing.
Application dependencies will be pinned only after a clean Monterey install
test; Phase 0 deliberately does not invent a lockfile without that test.

## Candidate signal comparison

### Audio features and models

| Candidate | Runtime/size profile | Monterey Intel practicality | Expected role | Decision |
| --- | --- | --- | --- | --- |
| RMS, peak, crest factor, zero crossing, silence ratio | Tiny; vectorized | Excellent | Boundaries, dropout, dynamics | Include in baseline |
| Log-mel/MFCC + deltas | Small STFT/matrix cost | Excellent | Speech/music texture and classifier input | Include in baseline |
| Spectral centroid/rolloff/flatness/flux/band energy | Small | Excellent | Rapid ad edits, jingles, transitions | Include in baseline |
| Loudness dynamics | Small if approximate; higher for full BS.1770 | Excellent | Relative compression/dynamic-range signal | Include rolling loudness proxy first |
| YAMNet embeddings | MobileNet-based TensorFlow model, 521 AudioSet classes; TensorFlow wheel about 236 MB | Technically possible with `tensorflow==2.15.1` CPython 3.11 x86-64/macOS 10.15+, but heavy and old | Transfer features for unfamiliar ads | Benchmark-only experiment, not core |
| YAMNet exported to ONNX/TFLite | Smaller runtime potential; conversion correctness work | Plausible with pinned ORT, but preprocessing/operators must be validated | Optional embedding extractor | Preferred pretrained experiment if reproducible |
| PANNs/CNN14 | PyTorch and larger network | Poor for always-on 2015 CPU | Rich embeddings | Reject initially |
| OpenL3/VGGish | TensorFlow-heavy and older packaging | Poor maintenance/runtime fit | Transfer embeddings | Reject initially |
| Small custom audio CNN | Training and data burden; compact inference | Runtime plausible only after dataset exists | Future learned representation | Defer until labeled corpus is sufficient |

YAMNet predicts general sound events, not advertisements. Its class scores must
not be treated as ad probabilities. If benchmarked, use its embeddings as inputs
to a locally trained classifier and evaluate unseen sessions. The official
[YAMNet tutorial](https://www.tensorflow.org/hub/tutorials/yamnet) describes its
521 AudioSet classes. PyPI publishes a
[TensorFlow 2.15.1 CPython 3.11 x86-64 wheel for macOS 10.15+](https://pypi.org/project/tensorflow/2.15.1/),
but the footprint and aging pin make it an optional experiment.

### Video features and models

| Candidate | Runtime profile at proposed cadence | Value | Decision |
| --- | --- | --- | --- |
| Luma/black/uniform/frozen-frame statistics | Tiny at 2 fps downscaled | Capture health and boundary cues | Required |
| HSV histograms and histogram deltas | Tiny | Global visual change | Required |
| Edge density and edge delta | Small | Graphics/text density and transitions | Required |
| Scene-cut score / shot-change rate | Small | Ads often have faster editing; also useful boundary cue | Required |
| Persistent corner-logo template/edge evidence | Small, calibrated ROI | Useful supporting program evidence | Optional rule signal, never sole architecture |
| OCR on sampled/triggered frames | Bursty and potentially expensive | Price/CTA/promo text | Experiment only after baseline; never continuous |
| Perceptual frame hashes | Tiny | Repeated slate/creative support and duplicate review | Include as auxiliary evidence |
| MobileNetV3-small embeddings via ONNX | Moderate CPU, model/runtime pin | Transfer features with compact encoder | Benchmark at 0.5-1 fps |
| EfficientNet-Lite0 | Higher than MobileNet-small | Potentially better representation | Secondary benchmark only |
| CLIP/VLM | Too large/slow for continuous CPU inference | Semantic flexibility | Reject for live runtime |
| Object/logo detector | Annotation/training cost; brittle across networks | Specific network/logo evidence | Defer; calibrated templates first |

Pretrained image encoders are generic, not ad classifiers. Any candidate must
meet the performance budget and improve session-held-out safety metrics over
classical features. ONNX Runtime 1.19.2 has a
[CPython 3.11 universal2 wheel targeting macOS 11+](https://pypi.org/project/onnxruntime/1.19.2/).
The current package no longer ships Intel Monterey wheels, so this pin is a
known maintenance risk rather than a permanent foundation.

## Selected initial ML architecture

The initial ML path is intentionally lightweight but genuinely trainable:

1. Extract per-sample audio/video features continuously.
2. Aggregate over causal 1 s, 3 s, 10 s, and 30 s windows: mean, standard
   deviation, quantiles, slope, transition count, and missingness.
3. Train two unimodal classifiers and one early-fusion classifier. Start with
   regularized logistic regression as the interpretable baseline; compare
   histogram gradient boosting for non-linear interactions.
4. Calibrate probabilities using held-out-session calibration data (isotonic
   only with enough examples; otherwise sigmoid/Platt calibration).
5. Emit all three class probabilities. `UNKNOWN` training examples include
   actual capture failures and out-of-distribution/ambiguous segments, while
   hard runtime health gates remain authoritative.

This choice makes measurement possible before committing the machine to a
pretrained runtime. It also lets optional embeddings append versioned columns to
the same feature contract for an apples-to-apples replay comparison.

LightGBM is deferred. It may outperform scikit-learn boosting, but it adds a
native dependency without being necessary to establish the baseline. It earns a
place only if the measured gain is material.

## Fingerprinting approach

### Selection

Use a Shazam-style spectrogram landmark index inspired by the open
[audfprint implementation](https://github.com/dpwe/audfprint) and its
[MIREX description](https://www.music-ir.org/mirex/abstracts/2014/DP1.pdf).
Prominent time-frequency peaks are paired into hashes `(f1, f2, delta_t)`.
Reference hashes map to `(ad_id, anchor_time)`. Query hits vote into an
`(ad_id, reference_time - query_time)` offset histogram. The dominant aligned
cluster yields identity, confidence, and approximate playback position.

This is preferred over a single whole-clip perceptual hash because matching must
work from arbitrary positions in a 15-60 second ad. Chromaprint remains a useful
benchmark/duplicate tool; its stated use cases include full-file identification,
duplicate detection, and stream monitoring, but a local landmark index provides
more direct subclip alignment and threshold control. See the
[Chromaprint overview](https://acoustid.org/chromaprint).

### Pipeline

1. Convert capture audio to mono 11.025 or 16 kHz float PCM.
2. Apply deterministic gain normalization and STFT settings.
3. Find locally maximal peaks above an adaptive noise floor.
4. Pair peaks within bounded time/frequency fan-out and hash the tuple.
5. Store hashes in normalized SQLite tables initially; if query profiling shows
   SQLite is insufficient, replace the index adapter without changing the port.
6. Query a rolling 3-8 second window every second and retain continuity across
   successive matches.

### Enrollment and false-match safeguards

- Enrollment requires an explicit trusted user label or reviewed sample.
- Reject samples with insufficient landmark density, invalid capture, or too
  little duration.
- Query the candidate against the existing index first; flag likely duplicates
  for merge rather than silently adding them.
- Require a minimum aligned-hash count, aligned/query ratio, tight offset
  dispersion, runner-up margin, and continuity over successive windows.
- Common station bumps and low-information audio can be maintained in a denylist
  or ambiguity set.
- Thresholds are learned on non-matching program sessions, not copied from a
  paper. Audfprint reports that consistently timed hashes sharply reduce chance
  matches, but that is evidence for the method, not a threshold for this device.
- A match supplies strong commercial evidence but still cannot override invalid
  capture, a manual program override, or actuator safety policy.

Approximate position is the dominant time-offset estimate relative to the
reference ad. Store reference duration so the temporal layer can estimate
remaining creative time, but do not unmute solely from that estimate because
ads may be cut, sped, or followed immediately by another ad.

## Rule evidence

The rules-only baseline deliberately mirrors proven categories from commercial
detectors without copying their tuning:

- silence/near-silence and audio discontinuity;
- black/uniform frames and fade transitions;
- scene cuts and rolling shot-change rate;
- persistent logo appearance/disappearance when calibrated;
- histogram/edge discontinuity;
- aspect or active-picture change if observed;
- common break-duration priors, used weakly;
- fingerprint match evidence.

[Comskip](https://www.comskip.org/) demonstrates why a multi-signal timeline is
more useful than one threshold: it visualizes brightness, sound, uniformity,
scene changes, logo, aspect, and black frames. The new system treats these as
features/evidence rather than a monolithic hand-tuned decision engine.

## Temporal classification strategy

Three approaches will be implemented behind one replay interface:

| Approach | Strength | Weakness | Role |
| --- | --- | --- | --- |
| Directional hysteresis + duration/debounce rules | Transparent, no training data, deterministic | Many interacting thresholds; weak uncertainty model | Required baseline and safety shell |
| Sliding-window calibrated classifier | Learns cross-modal feature interactions; inexpensive | Windows do not enforce sequence consistency | Selected initial ML observation model |
| Three-state HMM with constrained transitions | Explicit persistence, missing observations, sequence likelihood | Assumptions may be too simple; needs honest fitting | Selected temporal candidate |

The initial production candidate is a calibrated sliding-window classifier whose
emissions pass through a three-state HMM/Viterbi or filtered-forward decoder.
The state transition matrix strongly favors persistence, permits
`PROGRAM <-> COMMERCIAL` and transitions through/from `UNKNOWN`, and is fitted
only from training sessions with priors/regularization. Missing detector output
is marginalized, not imputed as commercial. Hard capture-health failure forces
`UNKNOWN` before policy.

The HMM must beat the simpler hysteresis baseline on held-out sessions,
especially false-muted program seconds/hour and boundary latency. If it does not,
ship hysteresis. A neural sequence model is deferred until the dataset is large
and diverse enough to justify its extra failure modes.

### Decision policy

Temporal classification and action policy remain separate. Default conservative
policy proposal:

- enter a commercial break only after high commercial posterior persists for a
  configurable evidence duration and at least two independent evidence families
  agree, or a high-quality fingerprint persists;
- do not mute when health is invalid, output is stale, manual program override
  is active, setup is incomplete, or intervention safety is unavailable;
- recover to program with a lower duration/threshold than entry so false muting
  ends quickly;
- enforce maximum mute duration and a periodic watchdog lease renewal;
- after forced restoration, require a cooldown and fresh evidence before muting;
- log a human-readable explanation with the dominant contributions and gates.

Sensitivity presets alter a small policy profile, not arbitrary hidden detector
thresholds. Advanced settings expose versioned parameters with validation.

## Dataset and labeling design

### Storage model

| Entity | Key fields |
| --- | --- |
| `capture_session` | session ID, start/end UTC, monotonic origin, device/config revisions, app version |
| `media_span` | session, start/end PTS, health, artifact manifest, content hash, retention class |
| `feature_window` | session/interval, schema version, artifact hash or compact values, missing mask |
| `detector_run` | detector/model/config versions, input manifest, probability output, latency |
| `decision_event` | temporal output, policy version, action, explanations, actuator result |
| `label_revision` | span, label, boundary kind, confidence, author, created time, supersedes ID, notes |
| `fingerprint_ad` | ad ID/name, trusted label revision, reference duration, fingerprint version, status |
| `model_version` | immutable model hash, code/data/feature versions, parameters, metrics, status |
| `evaluation_run` | dataset manifest hash, candidate versions, metrics, environment, report paths |

Labels are append-only revisions. The effective label is the latest valid
revision, so corrections remain auditable. Boundary labels have their own exact
timestamp and uncertainty radius; they are not inferred from one-second class
bins.

### Retention

- Keep a configurable RAM ring, initially targeting 60 s before and 30 s after
  the current point.
- Persist feature/event metadata by default; media retention requires explicit
  settings.
- Event clips include pre/post roll around predicted transitions and manual
  labels. Encode low-resolution review video and mono review audio separately so
  either can be disabled.
- Labeled artifacts are protected from automatic deletion. Unlabeled artifacts
  follow age and disk-quota policies with a visible dry-run/report.
- Export creates an immutable manifest with hashes, label revisions, schema
  versions, and optional media; it never silently rewrites the source dataset.

### Labeling UX

The dashboard supports one-tap `COMMERCIAL`, `PROGRAM`, and `UNKNOWN`, explicit
start/end boundary adjustment, correction history, and "missed ad" selection
from the ring buffer. Fingerprint enrollment is offered only for a reviewed
commercial span and shows duplicate/quality checks first.

## Training and evaluation strategy

### Leakage-safe pipeline

1. Freeze a dataset manifest and effective label revision set.
2. Group by recording/session; where repeated broadcasts are known, group those
   together as well. Never split neighboring windows randomly.
3. Reserve a final test group before model selection. Use grouped cross-validation
   within training for model/threshold selection and a separate calibration
   group where data volume permits.
4. Fit all normalization, feature selection, missing-value treatment, calibration,
   and temporal parameters on training groups only.
5. Evaluate complete sessions causally through the same replay clock, queues,
   deadlines, temporal model, and decision policy used live.
6. Register the candidate and report; require explicit activation.

### Required comparisons

- rules only;
- fingerprint only;
- audio ML;
- video ML;
- multimodal ML;
- combined ML + rules + fingerprint + policy;
- each temporal candidate with otherwise identical evidence.

Optional embeddings are an ablation column, never bundled invisibly into the
multimodal result.

### Metrics and definitions

| Metric | Definition |
| --- | --- |
| Precision/recall/F1 | Time-weighted and event-level commercial classification, with `UNKNOWN` reported separately |
| Mute coverage | Commercial-labeled seconds muted / eligible commercial seconds |
| False-muted program s/h | Program-labeled muted seconds / labeled program hours |
| Mute-start latency | Action-effective time minus labeled break start; distribution, not just mean |
| Unmute latency | Action-effective time minus labeled program return; early unmute also reported |
| Boundary error | Signed nearest matched start/end error with unmatched-boundary count |
| Inference latency | p50/p95/p99 per detector and end-to-end evidence age |
| Resource use | Time series and p95 CPU/RSS over replay; dropped-window count |
| Unknown coverage | Unknown seconds and reason breakdown |

Reports include per-session metrics, aggregate macro/micro results, confusion
matrices including `UNKNOWN`, latency distributions, threshold curves, model and
dataset hashes, and failure-case timelines. No accuracy claim is allowed from
training metrics or from a single recording.

Promotion gates should initially be conservative and remain configurable. A
candidate must not regress false-muted program seconds/hour beyond the agreed
safety gate, must improve a declared primary metric with confidence intervals,
must meet resource budgets, and must pass shadow mode before activation.

## Reference architecture assessment

Useful patterns found in
[The Clicker](https://github.com/michigandrew/the-clicker):

- capture calibration and health check in the dashboard;
- a playback-controller abstraction;
- pyatv discovery/pairing and local credential persistence;
- shadow mode and conservative/balanced/aggressive presets;
- directional debounce, re-entry cooldown, and maximum-break safety reset;
- a timeline with detector confidence and manual mark/suggest workflow.

Patterns not adopted:

- detector and engine behavior coupled through module-level configuration;
- a video-only `OpenCV.VideoCapture` loop rather than synchronized A/V capture;
- commercial inference from logo absence without an explicit `UNKNOWN` state;
- fixed volume-down and volume-up command counts, which can change user volume;
- one calibrated content profile as the central abstraction;
- the web server, engine, persistence, and hardware lifecycle sharing implicit
  mutable state.

## Sources

- [FFmpeg AVFoundation input device](https://ffmpeg.org/ffmpeg-devices.html#avfoundation)
- [The Clicker](https://github.com/michigandrew/the-clicker)
- [Comskip](https://www.comskip.org/)
- [Audfprint repository](https://github.com/dpwe/audfprint)
- [Ellis, The 2014 LabROSA Audio Fingerprint System](https://www.music-ir.org/mirex/abstracts/2014/DP1.pdf)
- [Chromaprint](https://acoustid.org/chromaprint)
- [TensorFlow Hub YAMNet tutorial](https://www.tensorflow.org/hub/tutorials/yamnet)
- [TensorFlow 2.15.1 release artifacts](https://pypi.org/project/tensorflow/2.15.1/)
- [ONNX Runtime 1.19.2 release artifacts](https://pypi.org/project/onnxruntime/1.19.2/)
- [ONNX Runtime current install documentation](https://onnxruntime.ai/docs/install/)
- [pyatv scan, pair, and connect](https://pyatv.dev/development/scan_pair_and_connect/)
- [pyatv supported features](https://pyatv.dev/documentation/supported_features/)
- [pyatv audio API](https://pyatv.dev/development/audio/)
- [pyatv storage API](https://pyatv.dev/development/storage/)

