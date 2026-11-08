"""Classical local audio/video feature extraction."""

from .classical import ClassicalFeatureExtractor
from .windows import WindowAssembler

__all__ = ["ClassicalFeatureExtractor", "WindowAssembler"]
