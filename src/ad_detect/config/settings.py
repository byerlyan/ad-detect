"""TOML-backed settings with explicit validation."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ad_detect.domain import OperatingMode, TemporalStrategy


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApplicationSettings(StrictModel):
    name: str = "Ad Detect"
    environment: str = "development"


class CaptureSettings(StrictModel):
    source: str = "simulated"
    queue_capacity: int = Field(default=8, ge=1, le=1024)
    stale_after_ms: int = Field(default=1500, ge=100, le=60_000)
    max_av_skew_ms: int = Field(default=150, ge=1, le=10_000)
    ffmpeg_path: str = "ffmpeg"
    video_device: str = ""
    audio_device: str = ""
    width: int = Field(default=1280, ge=160, le=7680)
    height: int = Field(default=720, ge=120, le=4320)
    frame_rate: float = Field(default=30.0, gt=0, le=120)
    sample_rate_hz: int = Field(default=48_000, ge=8_000, le=192_000)
    channels: int = Field(default=2, ge=1, le=8)
    probe_seconds: float = Field(default=2.0, gt=0, le=30)


class FeatureSettings(StrictModel):
    schema_version: str = "classical-v1"
    silence_amplitude: float = Field(default=0.01, gt=0, lt=1)
    scene_cut_threshold: float = Field(default=0.35, gt=0, le=1)


class RuleSettings(StrictModel):
    version: str = "rules-v1"
    high_rms: float = Field(default=0.18, ge=0, le=1)
    high_spectral_flux: float = Field(default=0.20, ge=0, le=2)
    rapid_cut_rate: float = Field(default=0.35, ge=0, le=1)
    high_edge_density: float = Field(default=0.18, ge=0, le=1)
    commercial_threshold: float = Field(default=0.70, ge=0.5, le=1)


class FingerprintSettings(StrictModel):
    version: str = "landmark-v1"
    sample_rate_hz: int = Field(default=16_000, ge=8_000, le=48_000)
    fft_size: int = Field(default=1024, ge=256, le=4096)
    hop_size: int = Field(default=256, ge=64, le=2048)
    peaks_per_frame: int = Field(default=1, ge=1, le=20)
    peak_time_radius: int = Field(default=4, ge=1, le=32)
    peak_percentile: float = Field(default=75.0, ge=0, le=100)
    fanout: int = Field(default=3, ge=1, le=20)
    target_min_frames: int = Field(default=2, ge=1, le=100)
    target_max_frames: int = Field(default=80, ge=2, le=255)
    minimum_duration_seconds: float = Field(default=3.0, gt=0, le=120)
    query_window_seconds: float = Field(default=5.0, ge=3.0, le=15.0)
    minimum_landmarks: int = Field(default=30, ge=1)
    minimum_landmarks_per_second: float = Field(default=5.0, gt=0)
    minimum_unique_hashes: int = Field(default=20, ge=1)
    minimum_frequency_bins: int = Field(default=32, ge=2)
    offset_bin_ms: int = Field(default=32, ge=1, le=1000)
    minimum_aligned_hashes: int = Field(default=12, ge=1)
    minimum_aligned_ratio: float = Field(default=0.08, gt=0, le=1)
    maximum_offset_dispersion_ms: float = Field(default=45.0, gt=0)
    minimum_runner_up_margin: float = Field(default=1.5, ge=1)
    minimum_confidence: float = Field(default=0.65, ge=0, le=1)
    continuity_ticks: int = Field(default=2, ge=1, le=10)
    continuity_position_tolerance_ms: int = Field(default=1500, ge=0, le=10_000)

    @model_validator(mode="after")
    def fingerprint_ranges_are_coherent(self) -> FingerprintSettings:
        if self.hop_size > self.fft_size:
            raise ValueError("fingerprint hop_size must not exceed fft_size")
        if self.target_max_frames <= self.target_min_frames:
            raise ValueError("target_max_frames must exceed target_min_frames")
        if self.query_window_seconds < self.minimum_duration_seconds:
            raise ValueError("query window must cover minimum fingerprint duration")
        return self


class MLSettings(StrictModel):
    enabled: bool = False
    aggregation_schema: str = "aggregate-v1"
    inference_timeout_ms: int = Field(default=200, ge=10, le=10_000)
    active_model_path: Path | None = None
    use_registry: bool = False

    @model_validator(mode="after")
    def enabled_ml_requires_bundle(self) -> MLSettings:
        if self.enabled and self.active_model_path is None and not self.use_registry:
            raise ValueError("enabled ML requires active_model_path or use_registry=true")
        if self.active_model_path is not None and self.use_registry:
            raise ValueError("ML must select either active_model_path or the registry")
        return self


class TemporalSettings(StrictModel):
    strategy: TemporalStrategy = TemporalStrategy.HYSTERESIS
    evidence_deadline_ms: int = Field(default=1500, ge=10, le=60_000)
    sliding_window_ticks: int = Field(default=5, ge=2, le=120)
    sliding_decay: float = Field(default=0.8, gt=0, le=1)
    hmm_stay_probability: float = Field(default=0.92, ge=0.5, lt=1)


class DetectionSettings(StrictModel):
    tick_interval_ms: int = Field(default=1000, ge=50, le=60_000)
    enter_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    exit_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    enter_ticks: int = Field(default=3, ge=1, le=120)
    exit_ticks: int = Field(default=2, ge=1, le=120)
    maximum_mute_seconds: int = Field(default=300, ge=1, le=3600)
    reentry_cooldown_seconds: int = Field(default=10, ge=0, le=300)
    mute_lease_ms: int = Field(default=3000, ge=20, le=60_000)
    watchdog_interval_ms: int = Field(default=250, ge=10, le=10_000)

    @model_validator(mode="after")
    def entry_must_be_conservative(self) -> DetectionSettings:
        if self.enter_threshold < self.exit_threshold:
            raise ValueError("enter_threshold must be >= exit_threshold")
        return self


class SensitivityProfile(StrictModel):
    enter_threshold: float = Field(ge=0, le=1)
    exit_threshold: float = Field(ge=0, le=1)
    enter_ticks: int = Field(ge=1, le=120)
    exit_ticks: int = Field(ge=1, le=120)

    @model_validator(mode="after")
    def thresholds_are_coherent(self) -> SensitivityProfile:
        if self.enter_threshold < self.exit_threshold:
            raise ValueError("enter_threshold must be >= exit_threshold")
        return self


class InterventionSettings(StrictModel):
    mode: OperatingMode = OperatingMode.SHADOW
    automatic_muting: bool = False
    adapter: str = "simulated"
    device_id: str = ""
    credentials_path: Path = Path("var/pyatv.conf")
    discovery_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    @model_validator(mode="after")
    def automatic_mode_requires_consent(self) -> InterventionSettings:
        if self.mode is OperatingMode.AUTOMATIC and not self.automatic_muting:
            raise ValueError("automatic mode requires automatic_muting=true")
        return self


class StorageSettings(StrictModel):
    database_path: Path = Path("var/ad-detect.sqlite3")
    artifact_path: Path = Path("var/artifacts")
    rolling_buffer_seconds: int = Field(default=90, ge=5, le=3600)
    artifact_quota_bytes: int = Field(default=1_073_741_824, ge=1_048_576)


class ServerSettings(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65_535)


class Settings(StrictModel):
    application: ApplicationSettings = ApplicationSettings()
    capture: CaptureSettings = CaptureSettings()
    features: FeatureSettings = FeatureSettings()
    rules: RuleSettings = RuleSettings()
    fingerprint: FingerprintSettings = FingerprintSettings()
    ml: MLSettings = MLSettings()
    temporal: TemporalSettings = TemporalSettings()
    detection: DetectionSettings = DetectionSettings()
    intervention: InterventionSettings = InterventionSettings()
    storage: StorageSettings = StorageSettings()
    server: ServerSettings = ServerSettings()


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _environment_overlay() -> dict[str, Any]:
    """Read a deliberately small, documented environment override surface."""
    overlay: dict[str, Any] = {}
    if value := os.getenv("AD_DETECT_DATABASE_PATH"):
        overlay.setdefault("storage", {})["database_path"] = value
    if value := os.getenv("AD_DETECT_HOST"):
        overlay.setdefault("server", {})["host"] = value
    if value := os.getenv("AD_DETECT_PORT"):
        overlay.setdefault("server", {})["port"] = int(value)
    if value := os.getenv("AD_DETECT_MODE"):
        overlay.setdefault("intervention", {})["mode"] = value
    return overlay


def load_settings(path: str | Path = "configs/defaults.toml") -> Settings:
    config_path = Path(path)
    with config_path.open("rb") as stream:
        document = tomllib.load(stream)
    return Settings.model_validate(_deep_merge(document, _environment_overlay()))


def load_sensitivity_profiles(
    path: str | Path = "configs/presets.toml",
) -> dict[str, SensitivityProfile]:
    with Path(path).open("rb") as stream:
        document = tomllib.load(stream)
    return {
        str(name): SensitivityProfile.model_validate(payload)
        for name, payload in document.items()
    }
