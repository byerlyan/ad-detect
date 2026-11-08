"""Transactional configuration previews and setup invalidation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ad_detect.config import Settings
from ad_detect.data import SQLiteStore
from ad_detect.domain import OperatingMode, SETUP_STEP_ORDER, SetupStep

from .workflow import SetupWorkflow


@dataclass(frozen=True, slots=True)
class ConfigurationPreview:
    settings: Settings
    digest: str
    changed_fields: tuple[str, ...]
    invalidates_from: SetupStep | None
    restart_required: bool


class ConfigurationService:
    def __init__(
        self,
        store: SQLiteStore,
        settings: Settings,
        workflow: SetupWorkflow,
        *,
        created_at_ns: int,
    ) -> None:
        self.store = store
        self.workflow = workflow
        active_digest = store.active_config_digest()
        active_payload = (
            store.config_revision(active_digest) if active_digest is not None else None
        )
        if active_digest is not None and active_payload is not None:
            self._settings = Settings.model_validate(active_payload)
            self._digest = active_digest
        else:
            self._settings = settings
            self._digest = store.save_config_revision(
                settings.model_dump(mode="json"),
                created_at_ns=created_at_ns,
            )
            store.activate_config_revision(self._digest, activated_at_ns=created_at_ns)

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def digest(self) -> str:
        return self._digest

    def preview(self, patch: Mapping[str, Any], *, created_at_ns: int) -> ConfigurationPreview:
        current = self._settings.model_dump(mode="python")
        candidate = Settings.model_validate(_deep_merge(current, patch))
        if (
            candidate.intervention.mode is OperatingMode.AUTOMATIC
            and not self.workflow.snapshot().automatic_consent_available
        ):
            raise ValueError("automatic mode requires completed shadow safety consent")
        candidate_payload = candidate.model_dump(mode="json")
        digest = self.store.save_config_revision(candidate_payload, created_at_ns=created_at_ns)
        changed = tuple(_changed_fields(self._settings.model_dump(mode="json"), candidate_payload))
        invalidates = _invalidation_step(changed)
        return ConfigurationPreview(
            settings=candidate,
            digest=digest,
            changed_fields=changed,
            invalidates_from=invalidates,
            restart_required=bool(changed),
        )

    def commit(
        self,
        patch: Mapping[str, Any],
        *,
        expected_digest: str,
        actor: str,
        at_ns: int,
    ) -> ConfigurationPreview:
        if expected_digest != self._digest:
            raise ValueError("configuration changed; refresh before committing")
        preview = self.preview(patch, created_at_ns=at_ns)
        self.store.activate_config_revision(preview.digest, activated_at_ns=at_ns)
        self._settings = preview.settings
        self._digest = preview.digest
        if preview.invalidates_from is not None:
            self.workflow.invalidate_from(
                preview.invalidates_from,
                config_digest=preview.digest,
                reason="dependent configuration changed",
                actor=actor,
                at_ns=at_ns,
            )
        return preview


def safe_settings_payload(settings: Settings) -> dict[str, Any]:
    payload = settings.model_dump(mode="json")
    payload["storage"] = {
        **payload["storage"],
        "database_path": "[server-managed]",
        "artifact_path": "[server-managed]",
    }
    payload["intervention"] = {
        **payload["intervention"],
        "credentials_path": "[server-managed]",
    }
    if payload["ml"].get("active_model_path") is not None:
        payload["ml"]["active_model_path"] = "[server-managed]"
    return payload


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _changed_fields(before: Mapping[str, Any], after: Mapping[str, Any], prefix: str = "") -> list[str]:
    result: list[str] = []
    for key in sorted(set(before) | set(after)):
        path = f"{prefix}.{key}" if prefix else key
        left = before.get(key)
        right = after.get(key)
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            result.extend(_changed_fields(left, right, path))
        elif left != right:
            result.append(path)
    return result


def _invalidation_step(changed: tuple[str, ...]) -> SetupStep | None:
    candidates: list[SetupStep] = []
    for field in changed:
        if field.startswith("capture.video") or field.startswith("capture.width") or field.startswith(
            "capture.height"
        ) or field.startswith("capture.frame_rate"):
            candidates.append(SetupStep.VIDEO)
        elif field.startswith("capture."):
            candidates.append(SetupStep.AUDIO)
        elif field.startswith("intervention."):
            candidates.append(SetupStep.APPLE_TV)
        elif field.startswith(("features.", "rules.", "fingerprint.", "ml.", "storage.")):
            candidates.append(SetupStep.READINESS)
        elif field.startswith(("temporal.", "detection.")):
            candidates.append(SetupStep.SHADOW)
    if not candidates:
        return None
    return min(candidates, key=SETUP_STEP_ORDER.index)
