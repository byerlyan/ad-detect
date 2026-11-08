"""Local status, timeline, feature, and label API with dashboard shell."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ad_detect import __version__
from ad_detect.app import DetectionRuntime
from ad_detect.config import Settings, load_settings
from ad_detect.data import SQLiteStore
from ad_detect.intervention import make_intervention


def _serialize(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
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
        runtime = DetectionRuntime(
            app_settings,
            event_store,
            session_id=session_id,
            intervention=make_intervention(app_settings.intervention),
        )
        event_store.save_config_revision(
            app_settings.model_dump(mode="json"),
            created_at_ns=0,
        )
        await runtime.start()
        application.state.runtime = runtime
        application.state.store = event_store
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

    @application.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(Path(__file__).with_name("static") / "index.html")

    @application.get("/api/v1/health")
    async def health(request: Request) -> dict[str, object]:
        runtime: DetectionRuntime = request.app.state.runtime
        snapshot = runtime.snapshot()
        return {
            "status": "ok" if snapshot.running else "stopped",
            "version": __version__,
            "source": app_settings.capture.source,
            "mode": app_settings.intervention.mode.value,
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

    return application
