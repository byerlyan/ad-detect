"""Classical local audio/video feature extraction."""

from .audio import mono_audio, resample_linear
from .classical import ClassicalFeatureExtractor
from .windows import WindowAssembler

__all__ = ["ClassicalFeatureExtractor", "WindowAssembler", "mono_audio", "resample_linear"]
