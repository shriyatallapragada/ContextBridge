"""Authenticated loopback API; neither the engine nor GUI imports this module."""

from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from contextbridge.chat_bridge import MAX_CHAT_CHARS
from contextbridge.core.sanitizer import sanitize
from contextbridge.dashboard import (
    add_task,
    get_commits,
    get_intent,
    import_chat_tasks,
    list_tasks,
    overview,
    preview_chat_tasks,
    refresh_momentum,
    remove_task,
    set_intent,
    update_task,
)
from contextbridge.errors import ContextBridgeError, UserError
from contextbridge.project_map import export_mermaid
from contextbridge.service import ProjectService

LOOPBACK = "127.0.0.1"
UI_ENV_VAR = "CB_UI_DIR"
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_agent: str = Field(default="generic", max_length=80)
    from_agent: str | None = Field(default=None, max_length=80)
    budget: int = Field(default=2000, ge=120, le=12000)
    polish: bool = False
    force: bool = False


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    north_star: str = Field(min_length=1, max_length=4000)


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=600)
    source: str = Field(default="manual", max_length=40)


class TaskStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["open", "resolved", "dismissed", "superseded"]


class ChatImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_CHAT_CHARS)
    commit: bool = True


def _ui_directory() -> Path | None:
    """Where the built dashboard lives, if it has been built."""
    override = os.environ.get(UI_ENV_VAR)
    candidates = [Path(override)] if override else []
    here = Path(__file__).resolve()
    candidates += [here.parent.parent / "frontend" / "dist", here.parent / "web"]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


def create_app(root: Path) -> FastAPI:
    service = ProjectService.for_path(root)
    app = FastAPI(title="ContextBridge local API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.session_token = secrets.token_urlsafe(32)
    app.state.project_root = str(service.root)

    # Momentum is parsed from git on boot so the dashboard never needs a manual command.
    if service.is_initialized():
        with suppress(ContextBridgeError, OSError, sqlite3.Error):
            refresh_momentum(service)  # A broken history must not stop the server.

    def ready() -> ProjectService:
        """The service, with storage created on the first authenticated dashboard call."""
        if not service.is_initialized():
            service.init(install_hook=False)
        return service

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        # The built UI shell carries no project data; every API route still needs a token.
        path = request.url.path
        if path == "/ui" or path.startswith("/ui/"):
            return await call_next(request)
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            token.encode("utf-8"), app.state.session_token.encode("ascii")
        ):
            return JSONResponse(
                {"detail": "A valid session bearer token is required."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    @app.exception_handler(ContextBridgeError)
    async def project_error(request: Request, exc: ContextBridgeError) -> JSONResponse:
        return JSONResponse({"detail": sanitize(exc.message)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "Invalid request fields or values."}, status_code=422)

    @app.get("/")
    def index() -> dict[str, str]:
        return {"service": "ContextBridge", "api": "/v1", "authentication": "session bearer token"}

    @app.get("/v1/status")
    def status() -> dict[str, Any]:
        result: dict[str, Any] = {"project": service.name, "initialized": service.is_initialized()}
        if service.is_initialized():
            snapshot = service.snapshot()
            context = snapshot.context
            result.update(
                branch=context.branch,
                session=asdict(context.session),
                source_files_changed=context.project_files_changed,
                test_files_changed=context.test_files_changed,
                tests=asdict(context.test_run) if context.test_run else None,
                notes=[asdict(d) for d in context.decisions[:20]],
                last_handoff=snapshot.last_handoff,
            )
        return result

    # ----- dashboard: intent, tasks, momentum, chat import (all deterministic) -----

    @app.get("/v1/dashboard")
    def dashboard(refresh: bool = Query(default=False)) -> dict[str, Any]:
        return overview(ready(), refresh=refresh)

    @app.get("/v1/intent")
    def intent() -> dict[str, Any]:
        return get_intent(ready())

    @app.put("/v1/intent")
    def update_intent(body: IntentRequest) -> dict[str, Any]:
        return set_intent(ready(), body.north_star)

    @app.get("/v1/tasks")
    def tasks(
        status: Literal["open", "resolved", "dismissed", "superseded"] | None = Query(default=None),
        source: str | None = Query(default=None, max_length=40),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, Any]:
        return list_tasks(ready(), status=status, source=source, limit=limit)

    @app.post("/v1/tasks", status_code=201)
    def create_task(body: TaskRequest) -> dict[str, Any]:
        return add_task(ready(), body.content, source=body.source)

    @app.patch("/v1/tasks/{task_id}")
    def change_task(task_id: str, body: TaskStatusRequest) -> dict[str, Any]:
        return update_task(ready(), task_id, body.status)

    @app.delete("/v1/tasks/{task_id}")
    def delete_task(task_id: str) -> dict[str, Any]:
        return remove_task(ready(), task_id)

    @app.get("/v1/commits")
    def commits(
        limit: int = Query(default=15, ge=1, le=100), refresh: bool = Query(default=False)
    ) -> dict[str, Any]:
        return get_commits(ready(), limit=limit, refresh=refresh)

    @app.post("/v1/chat-import")
    def chat_import(body: ChatImportRequest) -> dict[str, Any]:
        """Parse a pasted transcript into tasks; ``commit: false`` previews first."""
        if not body.commit:
            return {"parsed": preview_chat_tasks(body.text), "committed": False}
        return import_chat_tasks(ready(), body.text, commit=True)

    @app.get("/v1/graph")
    def graph(
        view: Literal["overview", "module", "flow"] = "overview",
        module: str | None = Query(default=None, max_length=300),
        limit: int = Query(default=24, ge=2, le=60),
    ) -> dict[str, Any]:
        return service.project_graph(view, module=module, limit=limit)

    @app.get("/v1/mermaid", response_class=PlainTextResponse)
    def mermaid(
        view: Literal["overview", "module", "flow"] = "overview",
        module: str | None = Query(default=None, max_length=300),
        limit: int = Query(default=24, ge=2, le=60),
        include_ai: bool = False,
    ) -> str:
        return export_mermaid(
            service.project_graph(view, module=module, limit=limit), include_ai=include_ai
        )

    @app.get("/v1/summary")
    def summary(budget: int = Query(default=2000, ge=200, le=12000)) -> dict[str, Any]:
        result = service.summarize(cached_only=True, explain=True)
        return {**asdict(result), "text": result.render(budget)}

    def prepare(body: HandoffRequest) -> Any:
        if not service.is_initialized():
            service.init(install_hook=False)
        return service.prepare_handoff(
            body.from_agent,
            body.to_agent,
            budget=body.budget,
            use_llm=body.polish,
            force=body.force,
            notify=lambda message: print(sanitize(message), flush=True),
        )

    @app.post("/v1/handoffs/preview")
    def preview(body: HandoffRequest) -> dict[str, Any]:
        result = prepare(body)
        return {
            **asdict(result.handoff),
            "target": result.adapter.relative_path if result.adapter else None,
            "written": False,
        }

    @app.post("/v1/handoffs")
    def write_handoff(body: HandoffRequest) -> dict[str, Any]:
        result = prepare(body)
        committed = service.commit_handoff(result)
        return {
            **asdict(result.handoff),
            "written": True,
            "path": committed.written.relative_to(service.root).as_posix(),
        }

    @app.get("/v1/openapi")
    def openapi() -> dict[str, Any]:
        schema = app.openapi()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["SessionToken"] = {
            "type": "http",
            "scheme": "bearer",
        }
        schema["security"] = [{"SessionToken": []}]
        return schema

    # ----- static dashboard (shell only; data always needs the session token) -----

    ui_directory = _ui_directory()
    if ui_directory is not None:
        from fastapi.staticfiles import StaticFiles

        app.mount("/ui", StaticFiles(directory=str(ui_directory), html=True), name="ui")
    else:

        @app.get("/ui")
        def ui_missing() -> JSONResponse:
            return JSONResponse(
                {
                    "detail": (
                        "The dashboard has not been built yet. Run "
                        "`npm install && npm run build` inside frontend/."
                    )
                },
                status_code=404,
            )

    # Added last so it wraps the auth middleware: dev-server preflights work, and every
    # real request still carries the session token.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEV_ORIGINS),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    return app


def run_server(root: Path, *, port: int = 8765, host: str = LOOPBACK) -> None:
    if host != LOOPBACK:
        raise UserError("The local API may bind only to 127.0.0.1.")
    if not 1 <= port <= 65535:
        raise UserError("Choose a port between 1 and 65535.")
    import uvicorn

    app = create_app(root)
    print(f"ContextBridge API: http://{LOOPBACK}:{port}", flush=True)
    if _ui_directory() is not None:
        # The token travels in the URL fragment: the browser never sends it to the server,
        # and the dashboard picks it up on first load. Printed exactly once either way.
        token = app.state.session_token
        print(f"Dashboard: http://{LOOPBACK}:{port}/ui/#token={token}", flush=True)
        print(
            "Open the dashboard URL to use the API. Ctrl-C stops the server.",
            flush=True,
        )
    else:
        print(f"Session token: {app.state.session_token}", flush=True)
        print(
            "Send Authorization: Bearer <session token> on every request. Ctrl-C stops the server.",
            flush=True,
        )
    uvicorn.run(
        app,
        host=LOOPBACK,
        port=port,
        workers=1,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        log_level="warning",
    )
