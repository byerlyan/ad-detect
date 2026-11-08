import pytest

from ad_detect.capture import SimulatedCapture
from ad_detect.domain import CaptureHealth, Classification, TemporalStrategy
from ad_detect.temporal import HiddenMarkovFusion, HysteresisFusion, SlidingWindowFusion


def test_expired_evidence_forces_unknown_and_resets_hysteresis() -> None:
    tick = next(iter(SimulatedCapture([0.95])))
    fusion = HysteresisFusion(
        enter_threshold=0.8,
        exit_threshold=0.6,
        enter_ticks=1,
        exit_ticks=1,
        stale_after_ms=1500,
        max_av_skew_ms=150,
        evidence_deadline_ms=500,
    )
    expired_health = CaptureHealth(
        at_ns=tick.at_ns + 600_000_000,
        video_valid=True,
        audio_valid=True,
        stale_ms=0,
        av_skew_ms=0,
    )

    state = fusion.update(tick.evidence, expired_health)

    assert state.classification is Classification.UNKNOWN
    assert state.probabilities[Classification.COMMERCIAL] == 0
    assert "evidence expired" in " ".join(state.reasons)


def test_sliding_window_ages_out_old_commercial_evidence() -> None:
    fusion = SlidingWindowFusion(
        enter_threshold=0.7,
        exit_threshold=0.6,
        window_ticks=3,
        decay=1.0,
        stale_after_ms=1500,
        max_av_skew_ms=150,
        evidence_deadline_ms=1500,
    )

    states = [
        fusion.update(tick.evidence, tick.health).classification
        for tick in SimulatedCapture([0.9, 0.9, 0.1, 0.1])
    ]

    assert states == [
        Classification.COMMERCIAL,
        Classification.COMMERCIAL,
        Classification.COMMERCIAL,
        Classification.PROGRAM,
    ]


def test_hmm_emits_normalized_three_state_posterior_and_resets_on_gap() -> None:
    fusion = HiddenMarkovFusion(
        stay_probability=0.9,
        stale_after_ms=1500,
        max_av_skew_ms=150,
        evidence_deadline_ms=1500,
    )
    commercial, gap = SimulatedCapture([0.99, None])

    first = fusion.update(commercial.evidence, commercial.health)
    second = fusion.update(gap.evidence, gap.health)

    assert first.classification in Classification
    assert sum(first.probabilities.values()) == pytest.approx(1.0)
    assert second.classification is Classification.UNKNOWN
    assert second.probabilities[Classification.COMMERCIAL] == 0


def test_explanations_include_detector_contribution_and_version() -> None:
    fusion = HysteresisFusion(
        enter_threshold=0.8,
        exit_threshold=0.6,
        enter_ticks=1,
        exit_ticks=1,
        stale_after_ms=1500,
        max_av_skew_ms=150,
    )
    tick = next(iter(SimulatedCapture([0.9])))

    state = fusion.update(tick.evidence, tick.health)

    assert state.evidence_versions == {"simulated": "1"}
    assert "simulated contribution" in " ".join(state.reasons)
    assert set(TemporalStrategy) == {
        TemporalStrategy.HYSTERESIS,
        TemporalStrategy.SLIDING_WINDOW,
        TemporalStrategy.HMM,
    }


def test_invalid_optional_detector_is_excluded_when_fresh_evidence_remains() -> None:
    valid = next(iter(SimulatedCapture([0.95])))
    invalid = next(iter(SimulatedCapture([None])))
    fusion = HysteresisFusion(
        enter_threshold=0.8,
        exit_threshold=0.6,
        enter_ticks=1,
        exit_ticks=1,
        stale_after_ms=1500,
        max_av_skew_ms=150,
    )

    state = fusion.update((*valid.evidence, *invalid.evidence), valid.health)

    assert state.classification is Classification.COMMERCIAL
    assert "evidence is invalid" in " ".join(state.reasons)
