import pytest

from ad_detect.intervention import SimulatedIntervention
from ad_detect.ports import DesiredAudioState


@pytest.mark.asyncio
async def test_intervention_is_idempotent_and_rejects_stale_generations() -> None:
    intervention = SimulatedIntervention()
    first = await intervention.request(DesiredAudioState.MUTED, 2)
    duplicate = await intervention.request(DesiredAudioState.MUTED, 2)
    stale = await intervention.request(DesiredAudioState.RESTORED, 1)

    assert first.changed and first.acknowledged
    assert not duplicate.changed and duplicate.acknowledged
    assert not stale.acknowledged
    assert intervention.state is DesiredAudioState.MUTED
