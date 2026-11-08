import asyncio

import pytest

from ad_detect.app import DetectionRuntime
from ad_detect.capture import SimulatedCapture
from ad_detect.config import Settings, load_settings
from ad_detect.data import SQLiteStore
from ad_detect.domain import CaptureHealth, RecommendedAction
from ad_detect.intervention import SimulatedIntervention
from ad_detect.ports import DesiredAudioState


def _automatic_settings(*, mute_lease_ms: int = 3000) -> Settings:
    document = load_settings().model_dump(mode="python")
    document["intervention"] = {"mode": "automatic", "automatic_muting": True}
    document["detection"].update(
        {
            "enter_ticks": 1,
            "exit_ticks": 1,
            "mute_lease_ms": mute_lease_ms,
            "watchdog_interval_ms": 10,
        }
    )
    return Settings.model_validate(document)


@pytest.mark.asyncio
async def test_manual_program_override_restores_immediately_and_inhibits_renewal() -> None:
    intervention = SimulatedIntervention()
    with SQLiteStore(":memory:") as store:
        runtime = DetectionRuntime(
            _automatic_settings(),
            store,
            session_id="override",
            intervention=intervention,
        )
        await runtime.start()
        tick = next(iter(SimulatedCapture([0.95])))
        entered = await runtime.process_tick(tick)
        overridden = await runtime.set_manual_program_override(True)

        assert entered.action is RecommendedAction.MUTE
        assert overridden is not None
        assert overridden.action is RecommendedAction.RESTORE
        assert intervention.audio_state is DesiredAudioState.RESTORED
        assert "manual program override" in overridden.reasons

        repeated = await runtime.process_tick(tick)
        assert repeated.action is RecommendedAction.NONE
        assert intervention.audio_state is DesiredAudioState.RESTORED
        await runtime.stop()


@pytest.mark.asyncio
async def test_mute_lease_watchdog_restores_when_decisions_stop() -> None:
    intervention = SimulatedIntervention()
    with SQLiteStore(":memory:") as store:
        runtime = DetectionRuntime(
            _automatic_settings(mute_lease_ms=30),
            store,
            session_id="watchdog",
            intervention=intervention,
        )
        await runtime.start()
        decision = await runtime.process_tick(next(iter(SimulatedCapture([0.95]))))
        assert decision.action is RecommendedAction.MUTE
        assert intervention.audio_state is DesiredAudioState.MUTED

        await asyncio.sleep(0.08)

        assert intervention.audio_state is DesiredAudioState.RESTORED
        events = store.timeline(session_id="watchdog")
        assert any(event.kind == "intervention.watchdog" for event in events)
        assert any(
            event.kind == "intervention.action" and event.payload["desired"] == "restored"
            for event in events
        )
        await runtime.stop()


@pytest.mark.asyncio
async def test_expired_evidence_restores_an_active_mute() -> None:
    intervention = SimulatedIntervention()
    with SQLiteStore(":memory:") as store:
        runtime = DetectionRuntime(
            _automatic_settings(),
            store,
            session_id="expired",
            intervention=intervention,
        )
        await runtime.start()
        tick = next(iter(SimulatedCapture([0.95])))
        await runtime.process_tick(tick)
        expired_health = CaptureHealth(
            at_ns=tick.at_ns + 2_000_000_000,
            video_valid=True,
            audio_valid=True,
            stale_ms=0,
            av_skew_ms=0,
        )

        decision = await runtime.process_evidence(tick.evidence, expired_health)

        assert decision.action is RecommendedAction.RESTORE
        assert intervention.audio_state is DesiredAudioState.RESTORED
        assert "evidence expired" in " ".join(decision.reasons)
        await runtime.stop()


@pytest.mark.asyncio
async def test_shutdown_restores_an_active_mute() -> None:
    intervention = SimulatedIntervention()
    with SQLiteStore(":memory:") as store:
        runtime = DetectionRuntime(
            _automatic_settings(),
            store,
            session_id="shutdown",
            intervention=intervention,
        )
        await runtime.start()
        await runtime.process_tick(next(iter(SimulatedCapture([0.95]))))
        assert intervention.audio_state is DesiredAudioState.MUTED

        await runtime.stop()

        assert intervention.audio_state is DesiredAudioState.RESTORED
        assert any(
            event.kind == "intervention.shutdown_restore"
            for event in store.timeline(session_id="shutdown")
        )
