from ad_detect.capture import SimulatedCapture
from ad_detect.domain import Classification, OperatingMode, RecommendedAction
from ad_detect.policy import ConservativeDecisionPolicy
from ad_detect.temporal import HysteresisFusion


def make_fusion() -> HysteresisFusion:
    return HysteresisFusion(
        enter_threshold=0.85,
        exit_threshold=0.65,
        enter_ticks=3,
        exit_ticks=2,
        stale_after_ms=1500,
        max_av_skew_ms=150,
    )


def test_hysteresis_enters_and_recovers_directionally() -> None:
    fusion = make_fusion()
    states = [
        fusion.update(tick.evidence, tick.health).classification
        for tick in SimulatedCapture([0.9, 0.9, 0.9, 0.1, 0.1])
    ]
    assert states == [
        Classification.PROGRAM,
        Classification.PROGRAM,
        Classification.COMMERCIAL,
        Classification.COMMERCIAL,
        Classification.PROGRAM,
    ]


def test_missing_capture_forces_unknown_and_restoration() -> None:
    fusion = make_fusion()
    policy = ConservativeDecisionPolicy(maximum_mute_seconds=300)
    decisions = []
    for tick in SimulatedCapture([0.9, 0.9, 0.9, None]):
        state = fusion.update(tick.evidence, tick.health)
        decisions.append(
            policy.decide(
                state,
                mode=OperatingMode.AUTOMATIC,
                automatic_muting_enabled=True,
            )
        )

    assert decisions[-2].action is RecommendedAction.MUTE
    assert decisions[-1].classification is Classification.UNKNOWN
    assert decisions[-1].action is RecommendedAction.RESTORE
    assert not decisions[-1].break_active


def test_unknown_requires_fresh_commercial_entry_evidence() -> None:
    fusion = make_fusion()
    states = [
        fusion.update(tick.evidence, tick.health).classification
        for tick in SimulatedCapture([0.9, 0.9, 0.9, None, 0.9, 0.9, 0.9])
    ]
    assert states[-4:] == [
        Classification.UNKNOWN,
        Classification.PROGRAM,
        Classification.PROGRAM,
        Classification.COMMERCIAL,
    ]


def test_maximum_mute_duration_has_reentry_cooldown() -> None:
    fusion = make_fusion()
    policy = ConservativeDecisionPolicy(
        maximum_mute_seconds=1,
        reentry_cooldown_seconds=10,
    )
    decisions = [
        policy.decide(
            fusion.update(tick.evidence, tick.health),
            mode=OperatingMode.AUTOMATIC,
            automatic_muting_enabled=True,
        )
        for tick in SimulatedCapture([0.9, 0.9, 0.9, 0.9, 0.9])
    ]
    assert decisions[2].action is RecommendedAction.MUTE
    assert decisions[3].action is RecommendedAction.RESTORE
    assert decisions[4].action is RecommendedAction.NONE
    assert "re-entry cooldown inhibits mute" in decisions[4].reasons


def test_shadow_mode_never_emits_actuator_action() -> None:
    fusion = make_fusion()
    policy = ConservativeDecisionPolicy(maximum_mute_seconds=300)
    decisions = [
        policy.decide(
            fusion.update(tick.evidence, tick.health),
            mode=OperatingMode.SHADOW,
            automatic_muting_enabled=False,
        )
        for tick in SimulatedCapture([0.9, 0.9, 0.9])
    ]
    assert decisions[-1].break_active
    assert all(decision.action is RecommendedAction.NONE for decision in decisions)
