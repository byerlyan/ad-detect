import pytest

from ad_detect.config.settings import RuleSettings
from ad_detect.detectors import RulesDetector
from ad_detect.domain import Classification, FeatureWindow, feature_values


def window(values: dict[str, float], *, missing: frozenset[str] = frozenset()) -> FeatureWindow:
    return FeatureWindow(
        schema_version="classical-v1",
        session_id="s",
        start_ns=0,
        end_ns=1,
        values=feature_values(values),
        missing=missing,
    )


@pytest.mark.asyncio
async def test_rules_combine_independent_audio_video_signals() -> None:
    detector = RulesDetector(RuleSettings(), feature_schema="classical-v1")
    evidence = await detector.infer(
        window(
            {
                "audio.rms": 0.4,
                "audio.spectral_flux": 0.5,
                "audio.silence_ratio": 0.0,
                "video.scene_cut_flag.mean": 0.8,
                "video.edge_density.mean": 0.5,
                "video.colorfulness.mean": 0.6,
                "video.black_ratio.max": 0.0,
            }
        )
    )
    assert evidence.valid
    assert evidence.probabilities[Classification.COMMERCIAL] == 0.99
    assert len(evidence.explanation) >= 4


@pytest.mark.asyncio
async def test_boundary_signals_are_not_positive_ad_evidence_by_themselves() -> None:
    detector = RulesDetector(RuleSettings(), feature_schema="classical-v1")
    evidence = await detector.infer(
        window(
            {
                "audio.rms": 0.0,
                "audio.spectral_flux": 0.0,
                "audio.silence_ratio": 1.0,
                "video.scene_cut_flag.mean": 0.0,
                "video.edge_density.mean": 0.0,
                "video.colorfulness.mean": 0.0,
                "video.black_ratio.max": 1.0,
            }
        )
    )
    assert evidence.probabilities[Classification.COMMERCIAL] < 0.1


@pytest.mark.asyncio
async def test_missing_capture_produces_only_unknown_evidence() -> None:
    detector = RulesDetector(RuleSettings(), feature_schema="classical-v1")
    evidence = await detector.infer(window({}, missing=frozenset({"capture", "audio", "video"})))
    assert not evidence.valid
    assert evidence.probabilities[Classification.UNKNOWN] == 1.0
    assert evidence.probabilities[Classification.COMMERCIAL] == 0.0


@pytest.mark.asyncio
async def test_schema_mismatch_fails_closed() -> None:
    detector = RulesDetector(RuleSettings(), feature_schema="classical-v2")
    evidence = await detector.infer(window({}))
    assert not evidence.valid
    assert evidence.explanation == ("incompatible feature schema",)
