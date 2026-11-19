"""Authenticated loopback API; neither the engine nor GUI imports this module."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from contextbridge.chat_bridge import MAX_CHAT_CHARS
from contextbridge.config import LLM_PROVIDERS
from contextbridge.core.sanitizer import sanitize
from contextbridge.dashboard import (
    add_task,
    get_commits,
    get_intent,
    import_chat_tasks,
    list_notes,
    list_tasks,
    overview,
    preview_chat_tasks,
    refresh_momentum,
    remove_task,
    set_intent,
    update_task,
)
from contextbridge.errors import AdapterConflictError, ContextBridgeError, UserError
from contextbridge.project_map import export_mermaid
from contextbridge.providers import DEFAULT_ENDPOINTS, credentials
from contextbridge.providers.settings import ProviderSettingsService, ProviderStatus
from contextbridge.service import ChatService, PreparedHandoff, ProjectService, detect_test_command
from contextbridge.storage.db import parse_utc, utc_now

LOOPBACK = "127.0.0.1"
UI_ENV_VAR = "CB_UI_DIR"
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_agent: str = Field(default="generic", max_length=80)
    from_agent: str | None = Field(default=None, max_length=80)
    budget: int = Field(default=2000, ge=120, le=12000)
    polish: bool | None = None
    force: bool = False
    preview_id: str | None = Field(default=None, max_length=40)


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    north_star: str = Field(min_length=1, max_length=4000)


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


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


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    tag: str = Field(default="note", max_length=20)


class TestRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, max_length=500)


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["off", "local", "cloud"]
    provider: str | None = Field(default=None, max_length=40)
    model: str = Field(default="", max_length=200)
    endpoint: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    polish_handoffs: bool | None = None


class SettingsTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=200)
    endpoint: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["file", "component", "project"]
    path: str | None = Field(default=None, max_length=500)
    component: str | None = Field(default=None, max_length=300)
    max_requests: int = Field(default=20, ge=1, le=200)
    token_budget: int = Field(default=40000, ge=1000, le=1000000)
    confirm: bool = False


class ChatHandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_CHAT_CHARS)
    goal: str = Field(default="", max_length=1000)
    pinned: str = Field(default="", max_length=1000)
    budget: int = Field(default=6000, ge=2000, le=12000)


class ChatImportFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4000)


# ----- background run state -------------------------------------------------------
# One run at a time per app. The API only records what the services stream back;
# parsing, storage and model work all stay in the engine.

_RETRY_RE = re.compile(r"Retry in ([0-9.]+)s")
_REQUESTS_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*requests")
_TOKENS_RE = re.compile(r"([\d,]+)\s*/\s*[\d,]+\s*reserved tokens")
_WARNING_RE = re.compile(r"stopped|Retry in|budget reached|Wait budget reached", re.I)
_MAX_MESSAGES = 500
_MAX_OUTPUT_CHARS = 200_000


class _RunSlot:
    """Live state of one background operation; ``begin`` refuses a second run."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.run_id: str | None = None
        self.running = False
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error: str | None = None

    def begin(self) -> str:
        with self._lock:
            if self.running:
                raise HTTPException(status_code=409, detail="Another run is already in progress.")
            self.run_id = uuid.uuid4().hex[:12]
            self.running = True
            self.started_at = utc_now(precise=True)
            self.finished_at = None
            self.error = None
            return self.run_id

    def finish(self, error: str | None = None) -> None:
        with self._lock:
            self.running = False
            self.finished_at = utc_now(precise=True)
            self.error = error

    @property
    def done(self) -> bool:
        with self._lock:
            return self.run_id is not None and not self.running


class _TestRunSlot(_RunSlot):
    """Streamed output and the parsed report of the latest test run."""

    def __init__(self) -> None:
        super().__init__()
        self.command: str | None = None
        self.output = ""
        self.report: dict[str, Any] | None = None

    def begin_run(self, command: str) -> str:
        with self._lock:
            run_id = self.begin()
            self.command = command
            self.output = ""
            self.report = None
            return run_id

    def append(self, text: str) -> None:
        with self._lock:
            self.output = (self.output + text)[-_MAX_OUTPUT_CHARS:]

    def record(self, report: dict[str, Any]) -> None:
        with self._lock:
            self.report = report


class _ExplainSlot(_RunSlot):
    """Every notify message of the running explanation, with its timestamp."""

    def __init__(self) -> None:
        super().__init__()
        self.scope: str | None = None
        self.messages: list[dict[str, str]] = []
        self.summary: str | None = None

    def begin_scope(self, scope: str) -> str:
        with self._lock:
            run_id = self.begin()
            self.scope = scope
            self.messages = []
            self.summary = None
            return run_id

    def notify(self, text: str) -> None:
        with self._lock:
            self.messages.append({"text": sanitize(text), "at": utc_now(precise=True)})
            del self.messages[:-_MAX_MESSAGES]

    def record(self, summary: str | None) -> None:
        with self._lock:
            self.summary = summary


def _last_match(pattern: re.Pattern[str], text: str) -> tuple[int, ...] | None:
    """Groups of the last match (the most recent progress message wins)."""
    found: tuple[int, ...] | None = None
    for match in pattern.finditer(text):
        found = tuple(int(float(group.replace(",", ""))) for group in match.groups())
    return found


def explain_status(slot: _ExplainSlot) -> dict[str, Any]:
    """Shape one explanation run for the dashboard (parsing the engine's messages)."""
    texts = [message["text"] for message in slot.messages]
    blob = "\n".join([slot.summary or "", slot.error or "", *texts])
    requests = _last_match(_REQUESTS_RE, blob)
    tokens = _last_match(_TOKENS_RE, blob)
    retry = _last_match(_RETRY_RE, blob)
    warnings: list[str] = []
    for text in texts:
        if _WARNING_RE.search(text) and text not in warnings:
            warnings.append(text)
    return {
        "run_id": slot.run_id,
        "running": slot.running,
        "scope": slot.scope,
        "messages": list(slot.messages),
        "summary": slot.summary,
        "error": slot.error,
        "done": slot.done,
        "retry_in_seconds": float(retry[0]) if retry else None,
        "requests_used": requests[0] if requests else None,
        "requests_budget": requests[1] if requests else None,
        "tokens_reserved": tokens[0] if tokens else None,
        "cache_hits": sum(text.startswith("Cached ") for text in texts),
        "warnings": warnings,
    }


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

    # One background run at a time per app, for tests and for explanations.
    test_slot = _TestRunSlot()
    explain_slot = _ExplainSlot()
    handoff_lock = threading.Lock()
    latest_preview: tuple[str, HandoffRequest, PreparedHandoff, list[str]] | None = None
    request_lock = asyncio.Lock()

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
        # Keep project switches ordered with API operations; jobs retain their service.
        async with request_lock:
            return await call_next(request)

    @app.exception_handler(ContextBridgeError)
    async def project_error(request: Request, exc: ContextBridgeError) -> JSONResponse:
        return JSONResponse(
            {"detail": sanitize(exc.message)},
            status_code=409 if isinstance(exc, AdapterConflictError) else 400,
        )

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

    @app.post("/v1/project")
    def switch_project(body: ProjectRequest) -> dict[str, str]:
        nonlocal service, test_slot, explain_slot, latest_preview
        if test_slot.running or explain_slot.running:
            raise HTTPException(
                status_code=409,
                detail="Wait for the current run to finish before switching projects.",
            )
        path = Path(body.path).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise UserError("Enter the full path to an existing project folder.")
        candidate = ProjectService.for_path(path.resolve())
        if not candidate.is_initialized():
            candidate.init(install_hook=False)
        service = candidate
        app.state.project_root = str(service.root)
        test_slot, explain_slot = _TestRunSlot(), _ExplainSlot()
        latest_preview = None
        return {"root": str(service.root), "project": service.name}

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

    # ----- notes, working-tree changes, tests (all deterministic) ----------

    @app.get("/v1/notes")
    def notes(
        tag: str | None = Query(default=None, max_length=20),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, Any]:
        return list_notes(ready(), tag=tag, limit=limit)

    @app.post("/v1/notes", status_code=201)
    def create_note(body: NoteRequest) -> dict[str, Any]:
        decision = ready().add_note(body.text, body.tag).decision
        return {
            "id": decision.id,
            "content": decision.content,
            "tag": decision.tag,
            "created_at": decision.created_at,
        }

    @app.delete("/v1/notes/{note_id}")
    def delete_note(note_id: str) -> dict[str, Any]:
        # Notes and tasks are rows of the same decision log, so removal is shared.
        return remove_task(ready(), note_id)

    @app.get("/v1/changes")
    def changes() -> dict[str, Any]:
        return ready().working_changes()

    @app.get("/v1/tests/detect")
    def tests_detect() -> dict[str, Any]:
        return {"command": detect_test_command(ready().root)}

    @app.post("/v1/tests/run", status_code=202)
    def start_test_run(body: TestRunRequest) -> dict[str, Any]:
        svc = ready()
        run_id = test_slot.begin_run(body.command.strip())

        def work() -> None:
            error: str | None = None
            try:
                outcome = svc.run_tests(body.command, on_output=test_slot.append)
                test_slot.record(
                    {
                        "passed": outcome.report.passed,
                        "failed": outcome.report.failed,
                        "skipped": outcome.report.skipped,
                        "runner": outcome.report.runner,
                        "recognized": outcome.report.recognized,
                        "failures": [
                            {key: sanitize(value) for key, value in failure.items()}
                            for failure in outcome.report.failures
                        ],
                        "exit_code": outcome.result.exit_code,
                    }
                )
            except ContextBridgeError as exc:
                error = sanitize(exc.message)
            except Exception:  # pragma: no cover - defensive: never leave the slot stuck.
                error = "The test run failed unexpectedly."
            test_slot.finish(error)

        threading.Thread(target=work, daemon=True, name="cb-test-run").start()
        return {"run_id": run_id}

    @app.get("/v1/tests/status")
    def test_status() -> dict[str, Any]:
        return {
            "run_id": test_slot.run_id,
            "running": test_slot.running,
            "command": test_slot.command,
            "output": test_slot.output,
            "report": test_slot.report,
            "started_at": test_slot.started_at,
            "finished_at": test_slot.finished_at,
            "error": test_slot.error,
        }

    @app.get("/v1/tests/history")
    def test_history(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        runs = ready().test_history(limit=limit)
        items = [
            {
                "id": run.id,
                "command": run.command,
                "exit_code": run.exit_code,
                "passed": run.passed,
                "failed": run.failed,
                "skipped": run.skipped,
                "ran_at": run.timestamp,
            }
            for run in runs
        ]
        age: int | None = None
        if runs:
            age = max(int((datetime.now(UTC) - parse_utc(runs[0].timestamp)).total_seconds()), 0)
        return {
            "runs": items,
            "last_run_age_seconds": age,
            "stale": age is None or age > 86400,
        }

    # ----- provider settings (never echo a key) ----------------------------

    def settings_payload() -> dict[str, Any]:
        cfg = ProviderSettingsService(context_dir=service.ws.context_dir).load()
        llm = cfg.llm
        key_endpoint = DEFAULT_ENDPOINTS.get(llm.provider, llm.base_url)
        return {
            "mode": {"none": "off", "ollama": "local"}.get(llm.provider, "cloud"),
            "model": llm.model,
            "endpoint": (
                ""
                if llm.provider == "none"
                else llm.ollama_host
                if llm.provider == "ollama"
                else key_endpoint
            ),
            "provider": llm.provider,
            "polish_handoffs": cfg.handoff.polish,
            "has_api_key": llm.provider in {"openai-compatible", "groq", "openai"}
            and bool(credentials.get_key(key_endpoint)),
        }

    @app.get("/v1/settings")
    def get_settings() -> dict[str, Any]:
        ready()
        return settings_payload()

    @app.post("/v1/settings")
    def save_settings(body: SettingsRequest) -> dict[str, Any]:
        svc = ready()
        provider = {"off": "none", "local": "ollama", "cloud": "openai-compatible"}[body.mode]
        ProviderSettingsService(context_dir=svc.ws.context_dir).save(
            provider,
            model=body.model,
            endpoint=body.endpoint or "",
            api_key=body.api_key or "",
            polish_handoffs=body.polish_handoffs,
        )
        return settings_payload()

    @app.post("/v1/settings/test")
    def test_settings(body: SettingsTestRequest) -> dict[str, Any]:
        svc = ready()
        store = ProviderSettingsService(context_dir=svc.ws.context_dir)
        cfg = store.load()
        provider = body.provider or cfg.llm.provider
        if provider not in LLM_PROVIDERS:
            raise UserError(f"Unknown provider {provider!r}.")
        model = cfg.llm.model if body.model is None else body.model
        if body.endpoint is not None:
            endpoint = body.endpoint
        elif provider == "ollama":
            endpoint = cfg.llm.ollama_host
        else:
            endpoint = DEFAULT_ENDPOINTS.get(provider, cfg.llm.base_url)
        try:
            status = store.check(provider, model, endpoint, body.api_key or "")
        except Exception:  # A probe must answer, never surface a traceback.
            status = ProviderStatus(False, "The connection check failed unexpectedly.")
        return {"ok": status.ok, "message": status.message}

    @app.post("/v1/chat-import")
    def chat_import(body: ChatImportRequest) -> dict[str, Any]:
        """Parse a pasted transcript into tasks; ``commit: false`` previews first."""
        if not body.commit:
            return {"parsed": preview_chat_tasks(body.text), "committed": False}
        return import_chat_tasks(ready(), body.text, commit=True)

    # ----- conversation handoff (deterministic, no model) -----------------

    @app.post("/v1/chat/handoff")
    def chat_handoff(body: ChatHandoffRequest) -> dict[str, Any]:
        return ChatService.handoff(body.text, body.goal, body.pinned, body.budget)

    @app.post("/v1/chat/import-file")
    def chat_import_file(body: ChatImportFileRequest) -> dict[str, Any]:
        try:
            path = (service.root / Path(body.path).expanduser()).resolve()
            if not path.is_relative_to(service.root) or not path.is_file():
                raise UserError("Choose a PDF, Markdown, or text file inside this project.")
            imported = ChatService.import_file(path)
        except ModuleNotFoundError:
            raise UserError(
                "PDF import requires pypdf. Install the ContextBridge gui extra."
            ) from None
        except ValueError as exc:
            raise UserError(str(exc)) from None
        except OSError:
            raise UserError(
                "That file could not be read. Choose a PDF, Markdown, or text file."
            ) from None
        return {"text": imported.text, "warnings": [imported.notice] if imported.notice else []}

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

    # ----- explain pipeline (a model runs only when a run is started) -----

    @app.get("/v1/explain/notice")
    def explain_notice(
        scope: Literal["file", "project", "component"] = Query(default="project"),
        path: str | None = Query(default=None, max_length=500),
        component: str | None = Query(default=None, max_length=300),  # kept for the UI's URL
        max_requests: int = Query(default=20, ge=1, le=200),
        token_budget: int = Query(default=40000, ge=1000, le=1000000),
    ) -> dict[str, Any]:
        """The privacy confirmation payload; provider `none` answers 400 with its message."""
        svc = ready()
        if scope == "file":
            if not path:
                raise UserError("Choose a file to explain.")
            return {
                "text": svc.file_explanation_notice(path),
                "requests": svc.ws.load_config().summary.max_requests,
                "tokens": token_budget,
            }
        return {
            "text": svc.project_explanation_notice(max_requests, token_budget),
            "requests": max_requests,
            "tokens": token_budget,
        }

    @app.post("/v1/explain", status_code=202)
    def start_explain(body: ExplainRequest) -> dict[str, Any]:
        svc = ready()
        if not body.confirm:
            raise UserError("Confirm the privacy notice before any file leaves this computer.")
        if body.scope == "file" and not body.path:
            raise UserError("Choose a file to explain.")
        if body.scope == "component" and not body.component:
            raise UserError("Choose a component to explain.")
        # The notice checks the provider first: `none` is a 400 and no run starts.
        if body.scope == "file":
            svc.file_explanation_notice(body.path or "")
        else:
            svc.project_explanation_notice(body.max_requests, body.token_budget)
        run_id = explain_slot.begin_scope(body.scope)

        def work() -> None:
            error: str | None = None
            summary: str | None = None
            try:
                if body.scope == "file":
                    summary = svc.explain_file(
                        body.path or "", notify=explain_slot.notify, token_budget=body.token_budget
                    )
                else:
                    summary = svc.explain_project(
                        max_requests=body.max_requests,
                        token_budget=body.token_budget,
                        component=body.component if body.scope == "component" else None,
                        notify=explain_slot.notify,
                    )
            except ContextBridgeError as exc:
                error = sanitize(exc.message)
            except Exception:  # pragma: no cover - defensive: never leave the slot stuck.
                error = "The explanation failed unexpectedly; progress is cached."
            explain_slot.record(summary)
            explain_slot.finish(error)

        threading.Thread(target=work, daemon=True, name="cb-explain").start()
        return {"run_id": run_id}

    @app.get("/v1/explain/status")
    def explain_status_route() -> dict[str, Any]:
        return explain_status(explain_slot)

    def prepare(body: HandoffRequest) -> tuple[PreparedHandoff, list[str]]:
        """Build the handoff and collect its warnings; nothing is written yet."""
        if not service.is_initialized():
            service.init(install_hook=False)
        notices: list[str] = []

        def notify(message: str) -> None:
            clean = sanitize(message)
            notices.append(clean)
            print(clean, flush=True)

        result = service.prepare_handoff(
            body.from_agent,
            body.to_agent,
            budget=body.budget,
            use_llm=body.polish,
            force=body.force,
            notify=notify,
        )
        warnings = [message for message in notices if message.startswith("Warning")]
        return result, warnings

    def handoff_payload(
        result: PreparedHandoff, warnings: list[str], *, written: bool
    ) -> dict[str, Any]:
        """The existing handoff fields plus source, target and test evidence."""
        adapter = result.adapter
        context = result.context
        run = context.test_run
        source = result.from_label or "unknown"
        return {
            **asdict(result.handoff),
            "source_agent": result.from_label or "unknown",
            "source_label": source,
            "target": adapter.relative_path if adapter else None,
            # Absolute path: unambiguous next to the root-relative `target`.
            "target_path": str(service.root / adapter.relative_path) if adapter else None,
            "test": (
                None
                if run is None
                else {
                    "result": "passed" if run.exit_code == 0 else "failed",
                    "ran_at": run.timestamp,
                    "stale": context.tests_stale,
                }
            ),
            "warnings": warnings,
            "written": written,
        }

    @app.post("/v1/handoffs/preview")
    def preview(body: HandoffRequest) -> dict[str, Any]:
        nonlocal latest_preview
        result, warnings = prepare(body)
        preview_id = uuid.uuid4().hex
        with handoff_lock:
            latest_preview = (preview_id, body, result, warnings)
        return {**handoff_payload(result, warnings, written=False), "preview_id": preview_id}

    @app.post("/v1/handoffs")
    def write_handoff(body: HandoffRequest) -> dict[str, Any]:
        nonlocal latest_preview
        if body.preview_id:
            with handoff_lock:
                exclude = {"preview_id", "force"}
                if (
                    latest_preview is None
                    or latest_preview[0] != body.preview_id
                    or latest_preview[1].model_dump(exclude=exclude)
                    != body.model_dump(exclude=exclude)
                ):
                    raise UserError(
                        "This preview is no longer available. Preview again before writing."
                    )
                _, _, result, warnings = latest_preview
                if result.adapter:
                    result.adapter.force = body.force
                committed = service.commit_handoff(result)
                latest_preview = None
        else:
            result, warnings = prepare(body)
            committed = service.commit_handoff(result)
        payload = handoff_payload(result, warnings, written=True)
        payload["path"] = committed.written.relative_to(service.root).as_posix()
        return payload

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


def run_server(
    root: Path, *, port: int = 8765, host: str = LOOPBACK, open_browser: bool = False
) -> None:
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
        if open_browser:
            import webbrowser

            timer = threading.Timer(
                1, webbrowser.open, args=(f"http://{LOOPBACK}:{port}/ui/#token={token}",)
            )
            timer.daemon = True
            timer.start()
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
