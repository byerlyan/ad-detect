"""Audited model registry with explicit authorization and no auto-promotion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ad_detect.data import (
    ModelActivationRecord,
    RegisteredModelRecord,
    SQLiteStore,
)

from .model import load_model_bundle

REGISTER_MODEL = "model.register"
ACTIVATE_MODEL = "model.activate"


@dataclass(frozen=True, slots=True)
class AuthorizedActor:
    actor: str
    permissions: frozenset[str]

    def require(self, permission: str) -> None:
        if not self.actor.strip() or permission not in self.permissions:
            raise PermissionError(f"actor is not authorized for {permission}")


class ModelRegistry:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        feature_schema: str,
        aggregation_schema: str | None = None,
    ) -> None:
        self.store = store
        self.feature_schema = feature_schema
        self.aggregation_schema = aggregation_schema

    def _validate_aggregation_schema(self, actual: str) -> None:
        if self.aggregation_schema is not None and actual != self.aggregation_schema:
            raise ValueError("model aggregation schema is incompatible")

    def register(
        self,
        bundle_path: str | Path,
        evaluation_digest: str,
        *,
        authorization: AuthorizedActor,
        registered_at_ns: int,
        notes: str = "",
    ) -> RegisteredModelRecord:
        authorization.require(REGISTER_MODEL)
        _, manifest = load_model_bundle(
            bundle_path,
            expected_feature_schema=self.feature_schema,
        )
        self._validate_aggregation_schema(manifest.aggregation_schema)
        evaluation = self.store.evaluation_run(evaluation_digest)
        if evaluation is None or evaluation.status != "complete":
            raise ValueError("registration requires a complete evaluation run")
        if evaluation.dataset_digest != manifest.dataset_digest:
            raise ValueError("model and evaluation dataset digests differ")
        evaluated = evaluation.payload.get("model_digests", [])
        if not isinstance(evaluated, list) or manifest.digest not in evaluated:
            raise ValueError("evaluation run does not include this model bundle")
        record = RegisteredModelRecord(
            model_id=manifest.digest,
            bundle_path=str(Path(bundle_path).resolve()),
            dataset_digest=manifest.dataset_digest,
            evaluation_digest=evaluation_digest,
            feature_schema=manifest.feature_schema,
            aggregation_schema=manifest.aggregation_schema,
            modality=manifest.modality.value,
            algorithm=manifest.algorithm.value,
            registered_by=authorization.actor,
            registered_at_ns=registered_at_ns,
            notes=notes,
        )
        self.store.register_model(record)
        return record

    def activate(
        self,
        model_id: str,
        *,
        authorization: AuthorizedActor,
        reason: str,
        config_digest: str,
        at_ns: int,
        action: str = "activate",
    ) -> ModelActivationRecord:
        authorization.require(ACTIVATE_MODEL)
        if not reason.strip():
            raise ValueError("model activation requires a reason")
        record = self.store.registered_model(model_id)
        if record is None:
            raise ValueError(f"registered model {model_id!r} does not exist")
        try:
            _, manifest = load_model_bundle(
                record.bundle_path,
                expected_feature_schema=self.feature_schema,
            )
            self._validate_aggregation_schema(manifest.aggregation_schema)
        except Exception:
            self.store.record_model_activation_failure(
                model_id,
                actor=authorization.actor,
                reason=reason,
                config_digest=config_digest,
                at_ns=at_ns,
                action=action,
                explanation="registered bundle failed integrity or compatibility validation",
            )
            raise
        if manifest.digest != model_id:
            raise ValueError("registered model bundle identity changed")
        return self.store.activate_registered_model(
            model_id,
            actor=authorization.actor,
            reason=reason,
            config_digest=config_digest,
            at_ns=at_ns,
            action=action,
        )

    def rollback(
        self,
        activation_sequence: int,
        *,
        authorization: AuthorizedActor,
        reason: str,
        config_digest: str,
        at_ns: int,
    ) -> ModelActivationRecord:
        authorization.require(ACTIVATE_MODEL)
        prior = self.store.model_activation(activation_sequence)
        if prior is None:
            raise ValueError(f"activation {activation_sequence} does not exist")
        if prior.previous_model_id is None:
            raise ValueError("activation has no previous model to restore")
        return self.activate(
            prior.previous_model_id,
            authorization=authorization,
            reason=reason,
            config_digest=config_digest,
            at_ns=at_ns,
            action="rollback",
        )

    def models(self) -> list[RegisteredModelRecord]:
        return self.store.registered_models()
