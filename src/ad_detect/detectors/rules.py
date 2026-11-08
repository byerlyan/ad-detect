"""Transparent multi-signal rules-only baseline."""

from __future__ import annotations

from ad_detect.config.settings import RuleSettings
from ad_detect.domain import DetectorEvidence, FeatureWindow
from ad_detect.domain.models import probabilities


class RulesDetector:
    def __init__(self, settings: RuleSettings, *, feature_schema: str) -> None:
        self.settings = settings
        self.feature_schema = feature_schema

    async def infer(self, features: FeatureWindow) -> DetectorEvidence:
        if features.schema_version != self.feature_schema:
            return self._unknown(features, "incompatible feature schema")
        if "capture" in features.missing:
            return self._unknown(features, "capture health is invalid")
        if "audio" in features.missing or "video" in features.missing:
            return self._unknown(features, "multimodal rules require audio and video")

        values = features.values
        score = 0.05
        reasons: list[str] = []

        rms = values.get("audio.rms", 0.0)
        if rms >= self.settings.high_rms:
            score += 0.20
            reasons.append(f"high audio RMS ({rms:.3f})")

        flux = values.get("audio.spectral_flux", 0.0)
        if flux >= self.settings.high_spectral_flux:
            score += 0.25
            reasons.append(f"high spectral change ({flux:.3f})")

        cut_rate = values.get("video.scene_cut_flag.mean", 0.0)
        if cut_rate >= self.settings.rapid_cut_rate:
            score += 0.25
            reasons.append(f"rapid scene changes ({cut_rate:.3f})")

        edge_density = values.get("video.edge_density.mean", 0.0)
        if edge_density >= self.settings.high_edge_density:
            score += 0.15
            reasons.append(f"high visual edge density ({edge_density:.3f})")

        colorfulness = values.get("video.colorfulness.mean", 0.0)
        if colorfulness >= 0.30:
            score += 0.10
            reasons.append(f"high colorfulness ({colorfulness:.3f})")

        silence = values.get("audio.silence_ratio", 0.0)
        black = values.get("video.black_ratio.max", 0.0)
        if silence >= 0.90:
            score *= 0.5
            reasons.append("near-silence boundary signal")
        if black >= 0.90:
            score *= 0.5
            reasons.append("black-frame boundary signal")

        score = min(0.99, max(0.01, score))
        if score < self.settings.commercial_threshold:
            reasons.append("commercial evidence below rules threshold")
        return DetectorEvidence(
            detector_id="rules",
            detector_version=self.settings.version,
            window_start_ns=features.start_ns,
            window_end_ns=features.end_ns,
            probabilities=probabilities(1.0 - score, score, 0.0),
            valid=True,
            explanation=tuple(reasons),
            metadata={
                "feature_schema": features.schema_version,
                "rules_threshold": self.settings.commercial_threshold,
            },
        )

    def _unknown(self, features: FeatureWindow, reason: str) -> DetectorEvidence:
        return DetectorEvidence(
            detector_id="rules",
            detector_version=self.settings.version,
            window_start_ns=features.start_ns,
            window_end_ns=features.end_ns,
            probabilities=probabilities(0.0, 0.0, 1.0),
            valid=False,
            explanation=(reason,),
            metadata={"feature_schema": features.schema_version},
        )
