from pathlib import Path

import pytest
from pydantic import ValidationError

from ad_detect.config import Settings, load_settings
from ad_detect.domain import OperatingMode


def test_default_configuration_loads() -> None:
    settings = load_settings(Path("configs/defaults.toml"))
    assert settings.capture.source == "simulated"
    assert settings.intervention.mode is OperatingMode.SHADOW


def test_automatic_mode_requires_explicit_enable() -> None:
    document = load_settings().model_dump(mode="python")
    document["intervention"] = {"mode": "automatic", "automatic_muting": False}
    with pytest.raises(ValidationError, match="automatic mode requires"):
        Settings.model_validate(document)


def test_entry_threshold_cannot_be_less_than_exit_threshold() -> None:
    document = load_settings().model_dump(mode="python")
    document["detection"]["enter_threshold"] = 0.5
    document["detection"]["exit_threshold"] = 0.7
    with pytest.raises(ValidationError, match="enter_threshold"):
        Settings.model_validate(document)


def test_enabled_ml_requires_an_explicit_model_bundle() -> None:
    document = load_settings().model_dump(mode="python")
    document["ml"]["enabled"] = True
    with pytest.raises(ValidationError, match="active_model_path"):
        Settings.model_validate(document)


def test_ml_cannot_select_a_path_and_registry_at_the_same_time() -> None:
    document = load_settings().model_dump(mode="python")
    document["ml"].update(
        {
            "enabled": True,
            "active_model_path": Path("models/example"),
            "use_registry": True,
        }
    )
    with pytest.raises(ValidationError, match="either active_model_path or the registry"):
        Settings.model_validate(document)
