from pathlib import Path
from types import SimpleNamespace

import pytest
from pyatv.const import FeatureName, FeatureState, Protocol

from ad_detect.config.settings import InterventionSettings
from ad_detect.intervention.appletv import AppleTVIntervention
from ad_detect.ports import DesiredAudioState


class FakeStorage:
    def __init__(self) -> None:
        self.loaded = 0
        self.saved = 0

    async def load(self) -> None:
        self.loaded += 1

    async def save(self) -> None:
        self.saved += 1


class FakeFeatures:
    def __init__(self, available: set[FeatureName]) -> None:
        self.available = available

    def get_feature(self, feature: FeatureName) -> SimpleNamespace:
        state = FeatureState.Available if feature in self.available else FeatureState.Unsupported
        return SimpleNamespace(state=state)


class FakeAudio:
    def __init__(self, volume: float) -> None:
        self.volume = volume
        self.set_history: list[float] = []

    async def set_volume(self, level: float) -> None:
        self.volume = level
        self.set_history.append(level)


class FakeAppleTV:
    def __init__(self, *, volume: float = 35.0, safe_volume: bool = True) -> None:
        available = {FeatureName.VolumeUp, FeatureName.VolumeDown}
        if safe_volume:
            available.update({FeatureName.Volume, FeatureName.SetVolume})
        self.features = FakeFeatures(available)
        self.audio = FakeAudio(volume)
        self.listener = None
        self.closed = False

    def close(self) -> set[object]:
        self.closed = True
        return set()


class FakeConfig:
    identifier = "living-room-id"
    all_identifiers = ["living-room-id", "alternate-id"]
    name = "Living Room"
    address = "192.0.2.10"
    services = [
        SimpleNamespace(protocol=Protocol.Companion),
        SimpleNamespace(protocol=Protocol.AirPlay),
    ]


class FakePairing:
    device_provides_pin = True

    def __init__(self) -> None:
        self.has_paired = False
        self.received_pin: int | None = None
        self.closed = False

    async def begin(self) -> None:
        pass

    def pin(self, pin: int) -> None:
        self.received_pin = pin

    async def finish(self) -> None:
        self.has_paired = self.received_pin == 1234

    async def close(self) -> None:
        self.closed = True


class FakeBackend:
    def __init__(self, atv: FakeAppleTV | None = None) -> None:
        self.atv = atv or FakeAppleTV()
        self.pairing = FakePairing()
        self.scan_identifiers: list[str | None] = []

    async def scan(self, loop: object, **kwargs: object) -> list[FakeConfig]:
        del loop
        identifier = kwargs.get("identifier")
        self.scan_identifiers.append(identifier if isinstance(identifier, str) else None)
        if identifier not in (None, "living-room-id"):
            return []
        return [FakeConfig()]

    async def connect(self, config: object, loop: object, **kwargs: object) -> FakeAppleTV:
        del config, loop, kwargs
        return self.atv

    async def pair(
        self,
        config: object,
        protocol: Protocol,
        loop: object,
        **kwargs: object,
    ) -> FakePairing:
        del config, protocol, loop, kwargs
        return self.pairing


def settings(tmp_path: Path) -> InterventionSettings:
    return InterventionSettings(credentials_path=tmp_path / "pyatv.conf")


@pytest.mark.asyncio
async def test_discovery_reports_protocols(tmp_path: Path) -> None:
    adapter = AppleTVIntervention(
        settings(tmp_path),
        backend=FakeBackend(),
        storage=FakeStorage(),
    )
    devices = await adapter.discover()
    assert devices[0].identifier == "living-room-id"
    assert devices[0].protocols == ("airplay", "companion")


@pytest.mark.asyncio
async def test_pairing_saves_credentials_only_after_success(tmp_path: Path) -> None:
    storage = FakeStorage()
    backend = FakeBackend()
    adapter = AppleTVIntervention(settings(tmp_path), backend=backend, storage=storage)

    session = await adapter.begin_pairing("living-room-id", "companion")
    paired = await adapter.finish_pairing(session.token, 1234)

    assert paired
    assert storage.saved == 1
    assert backend.pairing.closed


@pytest.mark.asyncio
async def test_absolute_volume_mute_is_idempotent_and_restores_observed_value(
    tmp_path: Path,
) -> None:
    atv = FakeAppleTV(volume=35.0)
    adapter = AppleTVIntervention(
        settings(tmp_path), backend=FakeBackend(atv), storage=FakeStorage()
    )
    await adapter.connect("living-room-id")
    assert adapter.capabilities().safe_automatic_mute

    muted = await adapter.request(DesiredAudioState.MUTED, 1)
    duplicate = await adapter.request(DesiredAudioState.MUTED, 1)
    restored = await adapter.request(DesiredAudioState.RESTORED, 2)

    assert muted.changed and muted.acknowledged
    assert not duplicate.changed and duplicate.acknowledged
    assert restored.changed and restored.acknowledged
    assert atv.audio.set_history == [0.0, 35.0]
    assert not adapter.mute_owned


@pytest.mark.asyncio
async def test_manual_volume_change_is_not_overwritten(tmp_path: Path) -> None:
    atv = FakeAppleTV(volume=40.0)
    adapter = AppleTVIntervention(
        settings(tmp_path), backend=FakeBackend(atv), storage=FakeStorage()
    )
    await adapter.connect("living-room-id")
    await adapter.request(DesiredAudioState.MUTED, 1)
    atv.audio.volume = 18.0

    result = await adapter.request(DesiredAudioState.RESTORED, 2)

    assert not result.changed and result.acknowledged
    assert atv.audio.set_history == [0.0]
    assert atv.audio.volume == 18.0


@pytest.mark.asyncio
async def test_automatic_mute_is_rejected_without_safe_capabilities(tmp_path: Path) -> None:
    atv = FakeAppleTV(safe_volume=False)
    adapter = AppleTVIntervention(
        settings(tmp_path), backend=FakeBackend(atv), storage=FakeStorage()
    )
    await adapter.connect("living-room-id")

    result = await adapter.request(DesiredAudioState.MUTED, 1)

    assert not result.acknowledged
    assert not result.changed
    assert atv.audio.set_history == []


@pytest.mark.asyncio
async def test_disconnect_restores_owned_mute_before_closing(tmp_path: Path) -> None:
    atv = FakeAppleTV(volume=22.0)
    adapter = AppleTVIntervention(
        settings(tmp_path), backend=FakeBackend(atv), storage=FakeStorage()
    )
    await adapter.connect("living-room-id")
    await adapter.request(DesiredAudioState.MUTED, 1)

    await adapter.disconnect()

    assert atv.audio.set_history == [0.0, 22.0]
    assert atv.closed
    assert not adapter.connected


@pytest.mark.asyncio
async def test_connection_loss_reconnects_and_restores_owned_mute(tmp_path: Path) -> None:
    atv = FakeAppleTV(volume=27.0)
    adapter = AppleTVIntervention(
        settings(tmp_path), backend=FakeBackend(atv), storage=FakeStorage()
    )
    await adapter.connect("living-room-id")
    await adapter.request(DesiredAudioState.MUTED, 1)
    assert atv.listener is not None
    atv.listener.connection_lost(RuntimeError("network changed"))
    assert not adapter.connected

    await adapter.reconnect(max_attempts=1, base_delay_seconds=0)

    assert adapter.connected
    assert not adapter.mute_owned
    assert atv.audio.set_history == [0.0, 27.0]
