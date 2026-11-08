"""SQLite-backed landmark enrollment and offset-aligned matching."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from statistics import median, pstdev
from uuid import UUID, uuid4

from ad_detect.config.settings import FingerprintSettings
from ad_detect.data import SQLiteStore
from ad_detect.domain import (
    AudioChunk,
    AudioLandmark,
    Classification,
    FingerprintMatch,
    FingerprintReference,
    FingerprintStatus,
    TrustedAdSample,
)

from .extractor import LandmarkExtractor


class FingerprintEnrollmentError(ValueError):
    pass


class LowInformationFingerprintError(FingerprintEnrollmentError):
    pass


class DuplicateFingerprintError(FingerprintEnrollmentError):
    def __init__(self, matches: tuple[FingerprintMatch, ...]) -> None:
        self.matches = matches
        super().__init__(f"sample matches existing fingerprint {matches[0].ad_id}")


@dataclass(frozen=True, slots=True)
class _Cluster:
    ad_id: UUID
    offset_bin: int
    offsets_by_query: dict[int, int]

    @property
    def count(self) -> int:
        return len(self.offsets_by_query)


class SQLiteFingerprintIndex:
    def __init__(self, store: SQLiteStore, settings: FingerprintSettings) -> None:
        self.store = store
        self.settings = settings
        self.extractor = LandmarkExtractor(settings)

    async def enroll(
        self,
        sample: TrustedAdSample,
        *,
        created_at_ns: int,
        allow_duplicate: bool = False,
    ) -> FingerprintReference:
        label = self.store.label_revision(sample.label_revision_id, effective_only=True)
        if label is None:
            raise FingerprintEnrollmentError("trusted label is missing or superseded")
        if label.label is not Classification.COMMERCIAL:
            raise FingerprintEnrollmentError("only trusted commercial labels can enroll")
        if label.session_id != sample.session_id:
            raise FingerprintEnrollmentError("sample and trusted label sessions differ")
        if sample.start_ns < label.start_ns or sample.end_ns > label.end_ns:
            raise FingerprintEnrollmentError("sample must be contained by its trusted label")

        samples = await asyncio.to_thread(self.extractor.samples, sample.audio)
        audio_seconds = samples.size / self.settings.sample_rate_hz
        span_seconds = (sample.end_ns - sample.start_ns) / 1_000_000_000
        if min(audio_seconds, span_seconds) < self.settings.minimum_duration_seconds:
            raise LowInformationFingerprintError("sample is shorter than minimum duration")
        tolerance = max(1.0, span_seconds * 0.10)
        if abs(audio_seconds - span_seconds) > tolerance:
            raise FingerprintEnrollmentError("audio duration does not match labeled span")
        landmarks = await asyncio.to_thread(self.extractor.extract_samples, samples)
        minimum_for_duration = max(
            self.settings.minimum_landmarks,
            round(self.settings.minimum_landmarks_per_second * audio_seconds),
        )
        if len(landmarks) < minimum_for_duration:
            raise LowInformationFingerprintError(
                f"sample has {len(landmarks)} landmarks; requires {minimum_for_duration}"
            )
        unique_hashes = len({landmark.hash_value for landmark in landmarks})
        if unique_hashes < self.settings.minimum_unique_hashes:
            raise LowInformationFingerprintError(
                f"sample has {unique_hashes} unique hashes; "
                f"requires {self.settings.minimum_unique_hashes}"
            )
        frequency_bins = {
            frequency_bin
            for landmark in landmarks
            for frequency_bin in (
                landmark.hash_value >> 20,
                (landmark.hash_value >> 8) & 0xFFF,
            )
        }
        if len(frequency_bins) < self.settings.minimum_frequency_bins:
            raise LowInformationFingerprintError(
                f"sample spans {len(frequency_bins)} frequency bins; "
                f"requires {self.settings.minimum_frequency_bins}"
            )
        duplicates = self._matches(landmarks)
        if duplicates and not allow_duplicate:
            raise DuplicateFingerprintError(duplicates)

        reference = FingerprintReference(
            ad_id=uuid4(),
            name=sample.name.strip(),
            label_revision_id=sample.label_revision_id,
            session_id=sample.session_id,
            start_ns=sample.start_ns,
            end_ns=sample.end_ns,
            duration_ms=round(span_seconds * 1000),
            fingerprint_version=self.settings.version,
            landmark_count=len(landmarks),
            status=FingerprintStatus.ACTIVE,
            created_at_ns=created_at_ns,
        )
        self.store.save_fingerprint_reference(reference, landmarks)
        return reference

    async def query(self, audio: tuple[AudioChunk, ...]) -> tuple[FingerprintMatch, ...]:
        landmarks = await asyncio.to_thread(self.extractor.extract, audio)
        return self._matches(landmarks)

    async def find_duplicates(self, sample: TrustedAdSample) -> tuple[FingerprintMatch, ...]:
        landmarks = await asyncio.to_thread(self.extractor.extract, sample.audio)
        return self._matches(landmarks)

    async def remove(self, ad_id: UUID) -> None:
        self.store.remove_fingerprint(ad_id)

    async def set_status(self, ad_id: UUID, status: FingerprintStatus) -> None:
        self.store.set_fingerprint_status(ad_id, status)

    def _matches(self, landmarks: tuple[AudioLandmark, ...]) -> tuple[FingerprintMatch, ...]:
        if not landmarks:
            return ()
        query_by_hash: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for query_index, landmark in enumerate(landmarks):
            query_by_hash[landmark.hash_value].append((query_index, landmark.anchor_ms))
        hits = self.store.fingerprint_hash_hits(set(query_by_hash))
        votes: dict[tuple[UUID, int], dict[int, int]] = defaultdict(dict)
        for hit in hits:
            for query_index, query_ms in query_by_hash[hit.hash_value]:
                offset = hit.anchor_ms - query_ms
                offset_bin = round(offset / self.settings.offset_bin_ms)
                bucket = votes[(hit.ad_id, offset_bin)]
                previous = bucket.get(query_index)
                bin_center = offset_bin * self.settings.offset_bin_ms
                if previous is None or abs(offset - bin_center) < abs(previous - bin_center):
                    bucket[query_index] = offset
        clusters = sorted(
            (
                _Cluster(ad_id=ad_id, offset_bin=offset_bin, offsets_by_query=offsets)
                for (ad_id, offset_bin), offsets in votes.items()
            ),
            key=lambda item: (-item.count, str(item.ad_id), item.offset_bin),
        )
        best_by_ad: dict[UUID, _Cluster] = {}
        for cluster in clusters:
            best_by_ad.setdefault(cluster.ad_id, cluster)

        references = {item.ad_id: item for item in self.store.fingerprints()}
        matches: list[FingerprintMatch] = []
        for ad_id, cluster in best_by_ad.items():
            reference = references.get(ad_id)
            if reference is None or reference.status is not FingerprintStatus.ACTIVE:
                continue
            runner_up = max(
                (other.count for other in clusters if other is not cluster),
                default=0,
            )
            margin = cluster.count / max(1, runner_up)
            aligned_ratio = cluster.count / len(landmarks)
            offsets = list(cluster.offsets_by_query.values())
            dispersion = float(pstdev(offsets)) if len(offsets) > 1 else 0.0
            if (
                cluster.count < self.settings.minimum_aligned_hashes
                or aligned_ratio < self.settings.minimum_aligned_ratio
                or dispersion > self.settings.maximum_offset_dispersion_ms
                or margin < self.settings.minimum_runner_up_margin
            ):
                continue
            aligned_score = min(1.0, cluster.count / self.settings.minimum_aligned_hashes)
            ratio_score = min(1.0, aligned_ratio / self.settings.minimum_aligned_ratio)
            margin_score = min(1.0, margin / self.settings.minimum_runner_up_margin)
            dispersion_score = max(
                0.0,
                1.0 - dispersion / (2 * self.settings.maximum_offset_dispersion_ms),
            )
            confidence = min(aligned_score, ratio_score, margin_score, dispersion_score)
            if confidence < self.settings.minimum_confidence:
                continue
            dominant_offset = round(median(offsets))
            query_end_ms = max(landmark.anchor_ms for landmark in landmarks)
            matches.append(
                FingerprintMatch(
                    ad_id=ad_id,
                    name=reference.name,
                    confidence=confidence,
                    aligned_hashes=cluster.count,
                    query_hashes=len(landmarks),
                    playback_position_ms=max(0, query_end_ms + dominant_offset),
                    offset_dispersion_ms=dispersion,
                    runner_up_margin=margin,
                    reference_version=reference.fingerprint_version,
                )
            )
        matches.sort(key=lambda item: (-item.confidence, -item.aligned_hashes, str(item.ad_id)))
        return tuple(matches)
