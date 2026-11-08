import pytest

from ad_detect.config.settings import (
    DetectionSettings,
    RuleSettings,
    TemporalSettings,
)
from ad_detect.domain import FeatureWindow, TemporalStrategy, feature_values
from ad_detect.learning import TemporalComparisonEvaluator

SECOND = 1_000_000_000


@pytest.mark.asyncio
async def test_all_temporal_strategies_use_the_same_causal_windows() -> None:
    windows = [
        FeatureWindow(
            schema_version="classical-v1",
            session_id="session",
            start_ns=index * SECOND,
            end_ns=(index + 1) * SECOND,
            values=feature_values(
                {
                    "audio.rms": signal,
                    "audio.spectral_flux": signal,
                    "video.scene_cut_flag.mean": signal,
                    "video.edge_density.mean": signal,
                    "video.colorfulness.mean": signal,
                }
            ),
        )
        for index, signal in enumerate((0.0, 0.9, 0.9, 0.9, 0.0))
    ]
    result = await TemporalComparisonEvaluator(
        RuleSettings(),
        DetectionSettings(),
        TemporalSettings(),
        feature_schema="classical-v1",
        stale_after_ms=1500,
        max_av_skew_ms=150,
    ).evaluate(windows, [])

    assert set(result.results) == set(TemporalStrategy)
    assert all(len(replay.predictions) == len(windows) for replay in result.results.values())
    assert all(
        [prediction.start_ns for prediction in replay.predictions]
        == [window.start_ns for window in windows]
        for replay in result.results.values()
    )
