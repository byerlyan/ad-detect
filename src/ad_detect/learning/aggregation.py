"""Causal multi-window aggregation for tabular ML candidates."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ad_detect.domain import FeatureWindow, feature_values


def aggregate_feature_windows(
    windows: Sequence[FeatureWindow],
    *,
    durations_seconds: tuple[int, ...] = (1, 3, 10, 30),
    schema_version: str = "aggregate-v1",
) -> list[FeatureWindow]:
    if not durations_seconds or any(duration <= 0 for duration in durations_seconds):
        raise ValueError("aggregation durations must be positive")
    if tuple(sorted(set(durations_seconds))) != durations_seconds:
        raise ValueError("aggregation durations must be unique and increasing")
    ordered = sorted(windows, key=lambda item: (item.session_id, item.end_ns))
    feature_keys = sorted({key for window in ordered for key in window.values})
    by_session: dict[str, list[FeatureWindow]] = {}
    for window in ordered:
        by_session.setdefault(window.session_id, []).append(window)

    result: list[FeatureWindow] = []
    for session_windows in by_session.values():
        for current_index, current in enumerate(session_windows):
            history = session_windows[: current_index + 1]
            values: dict[str, float] = {}
            missing: set[str] = set(current.missing)
            for duration_seconds in durations_seconds:
                duration_ns = duration_seconds * 1_000_000_000
                start_ns = current.end_ns - duration_ns
                selected = [
                    item
                    for item in history
                    if item.end_ns <= current.end_ns and item.end_ns > start_ns
                ]
                prefix = f"w{duration_seconds}"
                earliest = min(item.start_ns for item in selected)
                coverage = min(1.0, (current.end_ns - earliest) / duration_ns)
                values[f"{prefix}.meta.coverage"] = coverage
                values[f"{prefix}.meta.window_count"] = float(len(selected))
                for modality in ("audio", "video", "capture"):
                    values[f"{prefix}.missing.{modality}_ratio"] = sum(
                        modality in item.missing for item in selected
                    ) / len(selected)
                for key in feature_keys:
                    present = [
                        (item.end_ns, item.values[key]) for item in selected if key in item.values
                    ]
                    values[f"{prefix}.{key}.present_ratio"] = len(present) / len(selected)
                    if not present:
                        continue
                    times = np.asarray(
                        [(at_ns - present[0][0]) / 1_000_000_000 for at_ns, _ in present],
                        dtype=np.float64,
                    )
                    observations = np.asarray([value for _, value in present], dtype=np.float64)
                    values.update(_statistics(prefix, key, times, observations))
            result.append(
                FeatureWindow(
                    schema_version=f"{schema_version}:{current.schema_version}",
                    session_id=current.session_id,
                    start_ns=current.start_ns,
                    end_ns=current.end_ns,
                    values=feature_values(values),
                    missing=frozenset(missing),
                )
            )
    return result


def _statistics(
    prefix: str,
    key: str,
    times: np.ndarray,
    observations: np.ndarray,
) -> dict[str, float]:
    standard_deviation = float(observations.std())
    if observations.size > 1 and float(np.ptp(times)) > 0:
        centered_time = times - float(times.mean())
        slope = float(
            np.sum(centered_time * (observations - float(observations.mean())))
            / np.sum(centered_time * centered_time)
        )
        threshold = max(standard_deviation * 0.5, 1e-9)
        transitions = float(np.count_nonzero(np.abs(np.diff(observations)) > threshold))
    else:
        slope = 0.0
        transitions = 0.0
    return {
        f"{prefix}.{key}.mean": float(observations.mean()),
        f"{prefix}.{key}.std": standard_deviation,
        f"{prefix}.{key}.q25": float(np.quantile(observations, 0.25)),
        f"{prefix}.{key}.q50": float(np.quantile(observations, 0.50)),
        f"{prefix}.{key}.q75": float(np.quantile(observations, 0.75)),
        f"{prefix}.{key}.slope_per_second": slope,
        f"{prefix}.{key}.transition_count": transitions,
    }
