"""Time-aligned labels and leakage-safe recording-group splits."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ad_detect.domain import Classification, FeatureWindow, LabelRevision


@dataclass(frozen=True, slots=True)
class LabeledExample:
    features: FeatureWindow
    label: Classification
    label_revision_ids: tuple[str, ...]
    labeled_ratio: float


@dataclass(frozen=True, slots=True)
class SessionSplit:
    train: frozenset[str]
    calibration: frozenset[str]
    test: frozenset[str]

    def __post_init__(self) -> None:
        if self.train & self.calibration or self.train & self.test or self.calibration & self.test:
            raise ValueError("session splits must be disjoint")


def label_feature_windows(
    windows: Sequence[FeatureWindow],
    labels: Sequence[LabelRevision],
    *,
    minimum_labeled_ratio: float = 0.5,
) -> list[LabeledExample]:
    if not 0 < minimum_labeled_ratio <= 1:
        raise ValueError("minimum labeled ratio must be in (0, 1]")
    labels_by_session: dict[str, list[LabelRevision]] = {}
    for label in labels:
        labels_by_session.setdefault(label.session_id, []).append(label)
    result: list[LabeledExample] = []
    for window in windows:
        duration = window.end_ns - window.start_ns
        overlap_by_class = {classification: 0 for classification in Classification}
        revision_ids: set[str] = set()
        for label in labels_by_session.get(window.session_id, ()):
            overlap = max(
                0,
                min(window.end_ns, label.end_ns) - max(window.start_ns, label.start_ns),
            )
            if overlap:
                overlap_by_class[label.label] += overlap
                revision_ids.add(label.revision_id)
        maximum = max(overlap_by_class.values())
        if maximum / duration < minimum_labeled_ratio:
            continue
        winners = [label for label, overlap in overlap_by_class.items() if overlap == maximum]
        classification = winners[0] if len(winners) == 1 else Classification.UNKNOWN
        result.append(
            LabeledExample(
                features=window,
                label=classification,
                label_revision_ids=tuple(sorted(revision_ids)),
                labeled_ratio=maximum / duration,
            )
        )
    return result


def grouped_session_split(
    session_ids: Sequence[str],
    *,
    group_by_session: Mapping[str, str] | None = None,
    seed: str = "ad-detect-split-v1",
) -> SessionSplit:
    sessions = set(session_ids)
    if len(sessions) < 3:
        raise ValueError("at least three sessions are required for grouped splitting")
    mapping = {session: (group_by_session or {}).get(session, session) for session in sessions}
    groups: dict[str, set[str]] = {}
    for session, group in mapping.items():
        groups.setdefault(group, set()).add(session)
    if len(groups) < 3:
        raise ValueError("at least three independent recording groups are required")
    ordered_groups = sorted(
        groups,
        key=lambda group: hashlib.sha256(f"{seed}:{group}".encode()).hexdigest(),
    )
    test_count = max(1, round(len(ordered_groups) * 0.20))
    calibration_count = max(1, round(len(ordered_groups) * 0.20))
    if test_count + calibration_count >= len(ordered_groups):
        test_count = calibration_count = 1
    test_groups = set(ordered_groups[:test_count])
    calibration_groups = set(ordered_groups[test_count : test_count + calibration_count])
    train_groups = set(ordered_groups) - test_groups - calibration_groups

    def expand(selected: set[str]) -> frozenset[str]:
        return frozenset(session for group in selected for session in groups[group])

    return SessionSplit(
        train=expand(train_groups),
        calibration=expand(calibration_groups),
        test=expand(test_groups),
    )
