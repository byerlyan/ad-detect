"""TOML-backed settings with explicit validation."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ad_detect.domain import OperatingMode


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


class DetectionSettings(StrictModel):
    tick_interval_ms: int = Field(default=1000, ge=50, le=60_000)
    enter_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    exit_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    enter_ticks: int = Field(default=3, ge=1, le=120)
    exit_ticks: int = Field(default=2, ge=1, le=120)
    maximum_mute_seconds: int = Field(default=300, ge=1, le=3600)
    reentry_cooldown_seconds: int = Field(default=10, ge=0, le=300)

    @model_validator(mode="after")
    def entry_must_be_conservative(self) -> DetectionSettings:
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
