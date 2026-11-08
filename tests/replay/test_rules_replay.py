import pytest

from ad_detect.config.settings import DetectionSettings, RuleSettings
from ad_detect.domain import (
    BoundaryKind,
    Classification,
    FeatureWindow,
    LabelRevision,
    feature_values,
)
from ad_detect.learning import RulesReplayEvaluator

SECOND = 1_000_000_000


def _window(index: int, *, commercial_signal: bool = False) -> FeatureWindow:
    values = (
        {
            "audio.rms": 0.5,
            "audio.spectral_flux": 0.5,
            "video.scene_cut_flag.mean": 0.8,
            "video.edge_density.mean": 0.5,
            "video.colorfulness.mean": 0.5,
        }
        if commercial_signal
        else {}
    )
    return FeatureWindow(
        schema_version="classical-v1",
        session_id="session",
        start_ns=index * SECOND,
        end_ns=(index + 1) * SECOND,
        values=feature_values(values),
    )


def _label(
    revision_id: str,
    start: int,
    end: int,
    classification: Classification,
) -> LabelRevision:
    return LabelRevision(
        revision_id=revision_id,
        session_id="session",
        start_ns=start * SECOND,
        end_ns=end * SECOND,
        label=classification,
        boundary=BoundaryKind.NONE,
        confidence=1.0,
        author="fixture",
        created_at_ns=1,
    )


@pytest.mark.asyncio
async def test_rules_replay_is_causal_and_reports_safety_metrics() -> None:
    windows = [
        _window(0),
        _window(1),
        _window(2, commercial_signal=True),
        _window(3, commercial_signal=True),
        _window(4, commercial_signal=True),
        _window(5, commercial_signal=True),
        _window(6),
        _window(7),
    ]
    labels = [
        _label("program-a", 0, 2, Classification.PROGRAM),
        _label("commercial", 2, 6, Classification.COMMERCIAL),
        _label("program-b", 6, 8, Classification.PROGRAM),
    ]
    evaluator = RulesReplayEvaluator(
        RuleSettings(), DetectionSettings(), feature_schema="classical-v1"
    )

    result = await evaluator.evaluate(windows, labels)

    assert [item.classification for item in result.predictions] == [
        Classification.PROGRAM,
        Classification.PROGRAM,
        Classification.PROGRAM,
        Classification.PROGRAM,
        Classification.COMMERCIAL,
        Classification.COMMERCIAL,
        Classification.COMMERCIAL,
        Classification.PROGRAM,
    ]
    assert result.metrics.labeled_seconds == 7.0
    assert result.metrics.confusion_seconds["commercial"]["commercial"] == 1.0
    assert result.metrics.precision == pytest.approx(1 / 3)
    assert result.metrics.recall == 0.25
    assert result.metrics.commercial_mute_coverage == 0.25
    assert result.metrics.false_muted_program_seconds_per_hour == 2400.0
    assert result.metrics.mute_start_latency_seconds == 3.0
    assert result.metrics.unmute_latency_seconds == 2.0
    assert result.metrics.boundary_error_seconds == 2.5
    assert result.metrics.inference_max_ms >= 0
    assert result.metrics.peak_rss_bytes > 0


@pytest.mark.asyncio
async def test_capture_gap_forces_unknown_and_clears_entry_history() -> None:
    windows = [_window(0, commercial_signal=True), _window(1), _window(2, commercial_signal=True)]
    windows[1] = FeatureWindow(
        schema_version="classical-v1",
        session_id="session",
        start_ns=SECOND,
        end_ns=2 * SECOND,
        values=feature_values({}),
        missing=frozenset({"capture"}),
    )
    evaluator = RulesReplayEvaluator(
        RuleSettings(), DetectionSettings(), feature_schema="classical-v1"
    )

    result = await evaluator.evaluate(windows, [])

    assert result.predictions[1].classification is Classification.UNKNOWN
    assert result.predictions[2].classification is Classification.PROGRAM
