import pytest

from ad_detect.app import DetectionRuntime
from ad_detect.capture import SimulatedCapture
from ad_detect.config import Settings, load_settings
from ad_detect.data import SQLiteStore


def automatic_settings() -> Settings:
    document = load_settings().model_dump(mode="python")
    document["intervention"] = {"mode": "automatic", "automatic_muting": True}
    return Settings.model_validate(document)


async def run_once() -> list[tuple[int, str, object]]:
    with SQLiteStore(":memory:") as store:
        runtime = DetectionRuntime(automatic_settings(), store, session_id="deterministic")
        await runtime.start()
        for tick in SimulatedCapture([0.1, 0.9, 0.9, 0.9, 0.9, 0.1, 0.1, None]):
            await runtime.process_tick(tick)
        await runtime.stop()
        return [
            (event.at_ns, event.kind, event.payload)
            for event in store.timeline(session_id="deterministic")
        ]


@pytest.mark.asyncio
async def test_replay_is_deterministic() -> None:
    assert await run_once() == await run_once()


@pytest.mark.asyncio
async def test_runtime_mutes_and_restores_once() -> None:
    events = await run_once()
    actions = [payload for _, kind, payload in events if kind == "intervention.action"]
    assert [action["desired"] for action in actions] == ["muted", "restored"]
