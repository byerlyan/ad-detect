"""Deadline-aware conversion of detector evidence into one fused observation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ad_detect.domain import CaptureHealth, Classification, DetectorEvidence
from ad_detect.domain.models import probabilities


@dataclass(frozen=True, slots=True)
class FusedObservation:
    at_ns: int
    valid: bool
    probabilities: Mapping[Classification, float]
    reasons: tuple[str, ...]
    evidence_versions: Mapping[str, str]


def fuse_observation(
    evidence: Sequence[DetectorEvidence],
    health: CaptureHealth,
    *,
    stale_after_ms: float,
    max_av_skew_ms: float,
    evidence_deadline_ms: float,
) -> FusedObservation:
    reasons = list(_health_reasons(health, stale_after_ms, max_av_skew_ms))
    valid: list[DetectorEvidence] = []
    versions: dict[str, str] = {}
    for item in evidence:
        age_ms = (health.at_ns - item.window_end_ns) / 1_000_000
        if age_ms < 0:
            reasons.append(f"{item.detector_id} evidence is from the future")
        elif age_ms > evidence_deadline_ms:
            reasons.append(f"{item.detector_id} evidence expired at {age_ms:.1f} ms")
        elif not item.valid:
            reasons.append(f"{item.detector_id} evidence is invalid")
            reasons.extend(f"{item.detector_id}: {reason}" for reason in item.explanation)
        else:
            valid.append(item)
            versions[item.detector_id] = item.detector_version

    if reasons and _health_reasons(health, stale_after_ms, max_av_skew_ms):
        valid.clear()
        versions.clear()
    if not valid:
        if not reasons:
            reasons.append("no valid detector evidence before deadline")
        return FusedObservation(
            at_ns=health.at_ns,
            valid=False,
            probabilities=probabilities(0.0, 0.0, 1.0),
            reasons=tuple(dict.fromkeys(reasons)),
            evidence_versions=versions,
        )

    totals = {
        classification: sum(item.probabilities[classification] for item in valid) / len(valid)
        for classification in Classification
    }
    total = sum(totals.values())
    fused = probabilities(
        totals[Classification.PROGRAM] / total,
        totals[Classification.COMMERCIAL] / total,
        totals[Classification.UNKNOWN] / total,
    )
    for item in valid:
        reasons.append(
            f"{item.detector_id} contribution "
            f"p={item.probabilities[Classification.PROGRAM]:.3f} "
            f"c={item.probabilities[Classification.COMMERCIAL]:.3f} "
            f"u={item.probabilities[Classification.UNKNOWN]:.3f}"
        )
        reasons.extend(f"{item.detector_id}: {reason}" for reason in item.explanation)
    return FusedObservation(
        at_ns=health.at_ns,
        valid=True,
        probabilities=fused,
        reasons=tuple(dict.fromkeys(reasons)),
        evidence_versions=versions,
    )


def _health_reasons(
    health: CaptureHealth,
    stale_after_ms: float,
    max_av_skew_ms: float,
) -> tuple[str, ...]:
    reasons = list(health.reasons)
    if not health.video_valid:
        reasons.append("video invalid")
    if not health.audio_valid:
        reasons.append("audio invalid")
    if health.black_frame:
        reasons.append("black frame")
    if health.frozen_frame:
        reasons.append("frozen frame")
    if health.stale_ms > stale_after_ms:
        reasons.append("capture stale")
    if health.av_skew_ms is not None and abs(health.av_skew_ms) > max_av_skew_ms:
        reasons.append("A/V skew exceeds limit")
    return tuple(dict.fromkeys(reasons))
