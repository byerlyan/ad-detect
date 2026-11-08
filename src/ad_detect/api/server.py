"""Local status, timeline, feature, and label API with dashboard shell."""

from __future__ import annotations

import secrets
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ad_detect import __version__
from ad_detect.app import DetectionRuntime
from ad_detect.config import Settings, load_sensitivity_profiles, load_settings
from ad_detect.data import SQLiteStore
from ad_detect.domain import SetupStep
from ad_detect.intervention import make_intervention
from ad_detect.setup import (
    ConfigurationService,
    SetupHardwareService,
    SetupWorkflow,
    safe_settings_payload,
)


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SetupActionRequest(_StrictRequest):
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class _ConfigurationRequest(_StrictRequest):
    patch: dict[str, Any]
    expected_digest: str | None = None


class _PairingBeginRequest(_StrictRequest):
    device_id: str
    protocol: str


class _PairingFinishRequest(_StrictRequest):
    token: str
    pin: int | None = None


def _serialize(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def create_app(
    settings: Settings | None = None,
    *,
    store: SQLiteStore | None = None,
    session_id: str = "local-session",
) -> FastAPI:
    app_settings = settings or load_settings()
    owns_store = store is None

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        event_store = store or SQLiteStore(app_settings.storage.database_path)
        setup_workflow = SetupWorkflow(event_store)
        configuration = ConfigurationService(
            event_store,
            app_settings,
            setup_workflow,
            created_at_ns=time.time_ns(),
        )
        effective_settings = configuration.settings
        runtime = DetectionRuntime(
            effective_settings,
            event_store,
            session_id=session_id,
            intervention=make_intervention(effective_settings.intervention),
        )
        await runtime.start()
        application.state.runtime = runtime
        application.state.store = event_store
        application.state.setup_workflow = setup_workflow
        application.state.configuration = configuration
        application.state.setup_hardware = SetupHardwareService(
            configuration,
            setup_workflow,
        )
        try:
            yield
        finally:
            await runtime.stop()
            if owns_store:
                event_store.close()

    application = FastAPI(
        title=app_settings.application.name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )
    static_directory = Path(__file__).with_name("static")
    application.mount("/static", StaticFiles(directory=static_directory), name="static")
    application.state.setup_session = secrets.token_urlsafe(32)
    application.state.setup_csrf = secrets.token_urlsafe(32)

    @application.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(static_directory / "index.html")

    @application.get("/setup", include_in_schema=False)
    async def setup_page() -> FileResponse:
        return FileResponse(static_directory / "setup.html")

    @application.get("/api/v1/setup/bootstrap")
    async def setup_bootstrap(request: Request) -> JSONResponse:
        response = JSONResponse(
            {
                "csrf_token": request.app.state.setup_csrf,
                "loopback_only": app_settings.server.host in {"127.0.0.1", "::1", "localhost"},
            }
        )
        response.set_cookie(
            "ad_detect_setup",
            request.app.state.setup_session,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            max_age=3600,
        )
        return response

    @application.get("/api/v1/health")
    async def health(request: Request) -> dict[str, object]:
        runtime: DetectionRuntime = request.app.state.runtime
        snapshot = runtime.snapshot()
        return {
            "status": "ok" if snapshot.running else "stopped",
            "version": __version__,
            "source": request.app.state.configuration.settings.capture.source,
            "mode": request.app.state.configuration.settings.intervention.mode.value,
        }

    @application.get("/api/v1/state")
    async def state(request: Request) -> dict[str, object]:
        runtime: DetectionRuntime = request.app.state.runtime
        return _serialize(runtime.snapshot())  # type: ignore[return-value]

    @application.get("/api/v1/events")
    async def events(
        request: Request,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict[str, object]]:
        runtime: DetectionRuntime = request.app.state.runtime
        try:
            records = request.app.state.store.timeline(
                session_id=runtime.session_id,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return [
            {
                "sequence": record.sequence,
                "session_id": record.session_id,
                "at_ns": record.at_ns,
                "kind": record.kind,
                "payload": record.payload,
            }
            for record in records
        ]

    @application.get("/api/v1/features")
    async def features(
        request: Request,
        session: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict[str, object]]:
        runtime: DetectionRuntime = request.app.state.runtime
        records = request.app.state.store.feature_windows(session_id=session or runtime.session_id)
        return [_serialize(record) for record in records[-limit:]]  # type: ignore[misc]

    @application.get("/api/v1/labels")
    async def labels(
        request: Request,
        session: str | None = None,
        include_history: bool = False,
    ) -> list[dict[str, object]]:
        runtime: DetectionRuntime = request.app.state.runtime
        records = request.app.state.store.labels(
            session_id=session or runtime.session_id,
            effective_only=not include_history,
        )
        return [_serialize(record) for record in records]  # type: ignore[misc]

    @application.get("/api/v1/fingerprints")
    async def fingerprints(request: Request) -> list[dict[str, object]]:
        records = request.app.state.store.fingerprints()
        return [_serialize(record) for record in records]  # type: ignore[misc]

    @application.get("/api/v1/evaluations")
    async def evaluations(request: Request) -> list[dict[str, object]]:
        records = request.app.state.store.evaluation_runs()
        return [
            {
                "digest": record.digest,
                "dataset_digest": record.dataset_digest,
                "report_digest": record.report_digest,
                "status": record.status,
                "created_at_ns": record.created_at_ns,
                "completed_at_ns": record.completed_at_ns,
                "payload": _serialize(record.payload),
            }
            for record in records
        ]

    @application.get("/api/v1/models")
    async def models(request: Request) -> list[dict[str, object]]:
        records = request.app.state.store.registered_models()
        return [
            {
                "model_id": record.model_id,
                "dataset_digest": record.dataset_digest,
                "evaluation_digest": record.evaluation_digest,
                "feature_schema": record.feature_schema,
                "aggregation_schema": record.aggregation_schema,
                "modality": record.modality,
                "algorithm": record.algorithm,
                "registered_at_ns": record.registered_at_ns,
                "active": record.active,
            }
            for record in records
        ]

    @application.get("/api/v1/setup/state")
    async def setup_state(request: Request) -> dict[str, object]:
        workflow: SetupWorkflow = request.app.state.setup_workflow
        configuration: ConfigurationService = request.app.state.configuration
        return {
            "workflow": _serialize(workflow.snapshot()),
            "config_digest": configuration.digest,
        }

    @application.get("/api/v1/setup/config")
    async def setup_config(request: Request) -> dict[str, object]:
        configuration: ConfigurationService = request.app.state.configuration
        profiles = load_sensitivity_profiles()
        return {
            "digest": configuration.digest,
            "settings": safe_settings_payload(configuration.settings),
            "profiles": {
                name: profile.model_dump(mode="json") for name, profile in profiles.items()
            },
        }

    @application.post("/api/v1/setup/steps/{step}/acknowledge")
    async def acknowledge_setup_step(
        step: SetupStep,
        body: _SetupActionRequest,
        request: Request,
    ) -> dict[str, object]:
        _require_setup_csrf(request)
        workflow: SetupWorkflow = request.app.state.setup_workflow
        configuration: ConfigurationService = request.app.state.configuration
        try:
            result = workflow.acknowledge(
                step,
                config_digest=configuration.digest,
                payload=body.payload,
                actor="setup-browser",
                at_ns=time.time_ns(),
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _serialize(result)  # type: ignore[return-value]

    @application.post("/api/v1/setup/steps/{step}/skip")
    async def skip_setup_step(
        step: SetupStep,
        body: _SetupActionRequest,
        request: Request,
    ) -> dict[str, object]:
        _require_setup_csrf(request)
        workflow: SetupWorkflow = request.app.state.setup_workflow
        configuration: ConfigurationService = request.app.state.configuration
        try:
            result = workflow.skip(
                step,
                config_digest=configuration.digest,
                reason=body.reason,
                actor="setup-browser",
                at_ns=time.time_ns(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _serialize(result)  # type: ignore[return-value]

    @application.post("/api/v1/setup/config/preview")
    async def preview_setup_config(
        body: _ConfigurationRequest,
        request: Request,
    ) -> dict[str, object]:
        _require_setup_csrf(request)
        configuration: ConfigurationService = request.app.state.configuration
        try:
            preview = configuration.preview(body.patch, created_at_ns=time.time_ns())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _configuration_preview_payload(preview)

    @application.post("/api/v1/setup/config/commit")
    async def commit_setup_config(
        body: _ConfigurationRequest,
        request: Request,
    ) -> dict[str, object]:
        _require_setup_csrf(request)
        if body.expected_digest is None:
            raise HTTPException(status_code=400, detail="expected_digest is required")
        configuration: ConfigurationService = request.app.state.configuration
        try:
            preview = configuration.commit(
                body.patch,
                expected_digest=body.expected_digest,
                actor="setup-browser",
                at_ns=time.time_ns(),
            )
        except ValueError as exc:
            status = 409 if "refresh" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return _configuration_preview_payload(preview)

    @application.post("/api/v1/setup/capture/devices")
    async def setup_capture_devices(request: Request) -> list[dict[str, object]]:
        _require_setup_csrf(request)
        hardware: SetupHardwareService = request.app.state.setup_hardware
        try:
            records = await hardware.capture_devices()
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return [_serialize(record) for record in records]  # type: ignore[misc]

    @application.post("/api/v1/setup/capture/probe")
    async def setup_capture_probe(request: Request) -> dict[str, object]:
        _require_setup_csrf(request)
        hardware: SetupHardwareService = request.app.state.setup_hardware
        result = await hardware.capture_probe()
        return {
            "success": result.success,
            "duration_seconds": result.duration_seconds,
            "negotiated_video": result.negotiated_video,
            "negotiated_audio": result.negotiated_audio,
            "reason": result.reason,
        }

    @application.post("/api/v1/setup/appletv/devices")
    async def setup_appletv_devices(request: Request) -> list[dict[str, object]]:
        _require_setup_csrf(request)
        hardware: SetupHardwareService = request.app.state.setup_hardware
        try:
            records = await hardware.appletv_devices()
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return [_serialize(record) for record in records]  # type: ignore[misc]

    @application.post("/api/v1/setup/appletv/pair/begin")
    async def setup_pairing_begin(
        body: _PairingBeginRequest,
        request: Request,
    ) -> dict[str, object]:
        _require_setup_csrf(request)
        hardware: SetupHardwareService = request.app.state.setup_hardware
        try:
            session = await hardware.begin_pairing(body.device_id, body.protocol)
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _serialize(session)  # type: ignore[return-value]

    @application.post("/api/v1/setup/appletv/pair/finish")
    async def setup_pairing_finish(
        body: _PairingFinishRequest,
        request: Request,
    ) -> dict[str, bool]:
        _require_setup_csrf(request)
        hardware: SetupHardwareService = request.app.state.setup_hardware
        try:
            paired = await hardware.finish_pairing(body.token, body.pin)
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"paired": paired}

    @application.post("/api/v1/setup/readiness")
    async def setup_readiness(request: Request) -> dict[str, object]:
        _require_setup_csrf(request)
        hardware: SetupHardwareService = request.app.state.setup_hardware
        try:
            return hardware.readiness()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return application


def _require_setup_csrf(request: Request) -> None:
    session = request.cookies.get("ad_detect_setup", "")
    csrf = request.headers.get("x-csrf-token", "")
    expected_session = request.app.state.setup_session
    expected_csrf = request.app.state.setup_csrf
    if not (
        secrets.compare_digest(session, expected_session)
        and secrets.compare_digest(csrf, expected_csrf)
    ):
        raise HTTPException(status_code=403, detail="setup CSRF validation failed")
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    if origin is None or urlsplit(origin).netloc != host:
        raise HTTPException(status_code=403, detail="setup origin validation failed")


def _configuration_preview_payload(preview: object) -> dict[str, object]:
    from ad_detect.setup import ConfigurationPreview

    assert isinstance(preview, ConfigurationPreview)
    return {
        "digest": preview.digest,
        "changed_fields": list(preview.changed_fields),
        "invalidates_from": (
            None if preview.invalidates_from is None else preview.invalidates_from.value
        ),
        "restart_required": preview.restart_required,
        "settings": safe_settings_payload(preview.settings),
    }
