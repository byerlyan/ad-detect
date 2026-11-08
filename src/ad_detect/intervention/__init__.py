"""Playback intervention adapters."""

from ad_detect.config.settings import InterventionSettings
from ad_detect.ports import InterventionPort

from .simulated import SimulatedIntervention


def make_intervention(settings: InterventionSettings) -> InterventionPort:
    if settings.adapter == "simulated":
        return SimulatedIntervention()
    if settings.adapter == "appletv":
        try:
            from .appletv import AppleTVIntervention
        except ModuleNotFoundError as exc:
            if exc.name == "pyatv":
                raise RuntimeError(
                    "Apple TV support is not installed; install the 'appletv' extra"
                ) from exc
            raise
        return AppleTVIntervention(settings)
    raise ValueError(f"unknown intervention adapter {settings.adapter!r}")


__all__ = ["SimulatedIntervention", "make_intervention"]
