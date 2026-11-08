from ad_detect.domain import Classification, FeatureWindow, LabelRevision, feature_values
from ad_detect.learning import (
    aggregate_feature_windows,
    grouped_session_split,
    label_feature_windows,
)

SECOND = 1_000_000_000


def _feature(session: str, index: int, value: float) -> FeatureWindow:
    return FeatureWindow(
        schema_version="classical-v1",
        session_id=session,
        start_ns=index * SECOND,
        end_ns=(index + 1) * SECOND,
        values=feature_values({"audio.rms": value, "video.luma.mean": value * 10}),
        missing=frozenset({"video"}) if index == 1 else frozenset(),
    )


def _label(
    revision_id: str,
    session: str,
    start: int,
    end: int,
    classification: Classification,
) -> LabelRevision:
    from ad_detect.domain import BoundaryKind

    return LabelRevision(
        revision_id=revision_id,
        session_id=session,
        start_ns=start * SECOND,
        end_ns=end * SECOND,
        label=classification,
        boundary=BoundaryKind.NONE,
        confidence=1,
        author="fixture",
        created_at_ns=1,
    )


def test_aggregation_is_causal_and_reports_missingness() -> None:
    original = [_feature("s", index, value) for index, value in enumerate((1.0, 2.0, 3.0))]
    changed_future = [*original[:2], _feature("s", 2, 300.0)]

    first = aggregate_feature_windows(original, durations_seconds=(1, 3))
    second = aggregate_feature_windows(changed_future, durations_seconds=(1, 3))

    assert first[1] == second[1]
    assert first[2].values["w3.audio.rms.mean"] == 2.0
    assert first[2].values["w3.audio.rms.slope_per_second"] == 1.0
    assert first[2].values["w3.missing.video_ratio"] == 1 / 3
    assert first[0].values["w3.meta.coverage"] == 1 / 3


def test_label_alignment_uses_overlap_and_excludes_unlabeled_windows() -> None:
    windows = [_feature("s", index, 1.0) for index in range(3)]
    labels = [
        _label("program", "s", 0, 1, Classification.PROGRAM),
        _label("commercial", "s", 1, 3, Classification.COMMERCIAL),
    ]

    examples = label_feature_windows(windows, labels)

    assert [example.label for example in examples] == [
        Classification.PROGRAM,
        Classification.COMMERCIAL,
        Classification.COMMERCIAL,
    ]
    assert examples[1].label_revision_ids == ("commercial",)


def test_grouped_split_is_deterministic_disjoint_and_keeps_rebroadcasts_together() -> None:
    sessions = ["a", "a-repeat", "b", "c", "d", "e"]
    groups = {"a": "broadcast-a", "a-repeat": "broadcast-a"}

    first = grouped_session_split(sessions, group_by_session=groups)
    second = grouped_session_split(list(reversed(sessions)), group_by_session=groups)

    assert first == second
    assert first.train | first.calibration | first.test == frozenset(sessions)
    assert (
        ({"a", "a-repeat"} <= first.train)
        or ({"a", "a-repeat"} <= first.calibration)
        or ({"a", "a-repeat"} <= first.test)
    )
