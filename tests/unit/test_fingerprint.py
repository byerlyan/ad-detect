import numpy as np
import pytest

from ad_detect.config.settings import FingerprintSettings
from ad_detect.data import SQLiteStore
from ad_detect.domain import (
    AudioChunk,
    Classification,
    FingerprintStatus,
    MediaStamp,
    TrustedAdSample,
)
from ad_detect.fingerprint import (
    DuplicateFingerprintError,
    FingerprintEnrollmentError,
    LandmarkExtractor,
    LowInformationFingerprintError,
    SQLiteFingerprintIndex,
)

RATE = 16_000
SECOND = 1_000_000_000


def _creative(*, duration_seconds: int = 9, variant: int = 0) -> np.ndarray:
    time = np.arange(RATE * duration_seconds, dtype=np.float64) / RATE
    signal = np.zeros_like(time)
    random = np.random.default_rng(42 + variant)
    for second in range(duration_seconds):
        selected = (time >= second) & (time < second + 1)
        local = time[selected]
        first = 220 + variant * 17 + second * 73
        second_frequency = 900 + variant * 29 + second * 131
        signal[selected] = (
            0.45 * np.sin(2 * np.pi * first * local)
            + 0.30 * np.sin(2 * np.pi * second_frequency * local)
            + 0.08 * random.normal(size=selected.sum())
        )
    return np.asarray(np.clip(signal, -1.0, 1.0), dtype=np.float32)


def _chunk(samples: np.ndarray, *, session_id: str = "recording") -> AudioChunk:
    pcm = (np.clip(samples, -1.0, 1.0) * 30_000).astype("<i2")
    return AudioChunk(MediaStamp(session_id, 0, 0, 0), RATE, 1, "s16", pcm.tobytes())


def _commercial_label(store: SQLiteStore, *, duration_seconds: int = 9) -> str:
    return store.add_label(
        session_id="recording",
        start_ns=0,
        end_ns=duration_seconds * SECOND,
        label=Classification.COMMERCIAL,
        author="reviewer",
        created_at_ns=1,
    ).revision_id


def test_landmark_extraction_is_deterministic_and_rejects_silence() -> None:
    extractor = LandmarkExtractor(FingerprintSettings())
    creative = _chunk(_creative())

    first = extractor.extract((creative,))
    second = extractor.extract((creative,))
    silence = extractor.extract((_chunk(np.zeros(RATE * 4, dtype=np.float32)),))

    assert first == second
    assert 500 < len(first) < 5_000
    assert silence == ()


@pytest.mark.asyncio
async def test_arbitrary_subclip_matches_identity_position_gain_and_noise() -> None:
    signal = _creative()
    settings = FingerprintSettings()
    with SQLiteStore(":memory:") as store:
        label_id = _commercial_label(store)
        index = SQLiteFingerprintIndex(store, settings)
        reference = await index.enroll(
            TrustedAdSample(
                name="Example Creative",
                label_revision_id=label_id,
                session_id="recording",
                start_ns=0,
                end_ns=9 * SECOND,
                audio=(_chunk(signal),),
            ),
            created_at_ns=2,
        )
        random = np.random.default_rng(99)
        query = signal[2 * RATE : 7 * RATE] * 0.55
        query += random.normal(0.0, 0.005, query.size).astype(np.float32)

        matches = await index.query((_chunk(query, session_id="query"),))

        assert matches[0].ad_id == reference.ad_id
        assert matches[0].aligned_hashes >= settings.minimum_aligned_hashes
        assert matches[0].offset_dispersion_ms <= settings.maximum_offset_dispersion_ms
        assert matches[0].playback_position_ms == pytest.approx(7_000, abs=200)


@pytest.mark.asyncio
async def test_enrollment_requires_effective_commercial_label_and_information() -> None:
    with SQLiteStore(":memory:") as store:
        program = store.add_label(
            session_id="recording",
            start_ns=0,
            end_ns=9 * SECOND,
            label=Classification.PROGRAM,
            author="reviewer",
            created_at_ns=1,
        )
        index = SQLiteFingerprintIndex(store, FingerprintSettings())
        sample = TrustedAdSample(
            "Not an ad",
            program.revision_id,
            "recording",
            0,
            9 * SECOND,
            (_chunk(_creative()),),
        )
        with pytest.raises(FingerprintEnrollmentError, match="commercial"):
            await index.enroll(sample, created_at_ns=2)

        commercial_id = _commercial_label(store, duration_seconds=4)
        silence = TrustedAdSample(
            "Silence",
            commercial_id,
            "recording",
            0,
            4 * SECOND,
            (_chunk(np.zeros(RATE * 4, dtype=np.float32)),),
        )
        with pytest.raises(LowInformationFingerprintError, match="landmarks"):
            await index.enroll(silence, created_at_ns=3)

        time = np.arange(RATE * 4, dtype=np.float64) / RATE
        tone = np.asarray(0.5 * np.sin(2 * np.pi * 440 * time), dtype=np.float32)
        low_diversity = TrustedAdSample(
            "Stationary tone",
            commercial_id,
            "recording",
            0,
            4 * SECOND,
            (_chunk(tone),),
        )
        with pytest.raises(LowInformationFingerprintError, match="frequency bins"):
            await index.enroll(low_diversity, created_at_ns=4)


@pytest.mark.asyncio
async def test_duplicate_status_and_removal_management() -> None:
    signal = _creative()
    with SQLiteStore(":memory:") as store:
        label_id = _commercial_label(store)
        index = SQLiteFingerprintIndex(store, FingerprintSettings())
        sample = TrustedAdSample(
            "Original",
            label_id,
            "recording",
            0,
            9 * SECOND,
            (_chunk(signal),),
        )
        reference = await index.enroll(sample, created_at_ns=2)

        with pytest.raises(DuplicateFingerprintError) as duplicate:
            await index.enroll(sample, created_at_ns=3)
        assert duplicate.value.matches[0].ad_id == reference.ad_id

        await index.set_status(reference.ad_id, FingerprintStatus.DISABLED)
        assert await index.query((_chunk(signal[2 * RATE : 7 * RATE]),)) == ()
        await index.set_status(reference.ad_id, FingerprintStatus.ACTIVE)
        assert (await index.query((_chunk(signal[2 * RATE : 7 * RATE]),)))[
            0
        ].ad_id == reference.ad_id

        await index.remove(reference.ad_id)
        assert store.fingerprints() == []


@pytest.mark.asyncio
async def test_unrelated_program_audio_does_not_match() -> None:
    with SQLiteStore(":memory:") as store:
        label_id = _commercial_label(store)
        index = SQLiteFingerprintIndex(store, FingerprintSettings())
        await index.enroll(
            TrustedAdSample(
                "Known ad",
                label_id,
                "recording",
                0,
                9 * SECOND,
                (_chunk(_creative()),),
            ),
            created_at_ns=2,
        )

        assert await index.query((_chunk(_creative(duration_seconds=5, variant=10)),)) == ()
