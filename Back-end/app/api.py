import json
import time
import uuid
from typing import Any

from config.settings import SETTINGS
from persistence.postgres_app_store import PostgresAppStore
from persistence.postgres_run_store import (
    ActiveChatRunExistsError,
    ActiveRunLimitExceededError,
    PostgresRunStore,
    RunNotFoundError,
    RunRequeueError,
    TERMINAL_RUN_STATUSES,
)

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - optional until FastAPI is installed
    FastAPI = None
    HTTPException = Exception
    CORSMiddleware = None
    StreamingResponse = None

    class BaseModel:  # type: ignore[override]
        pass

    def Field(default=None, **kwargs):  # type: ignore[override]
        del kwargs
        return default


def _require_fastapi():
    if FastAPI is None:
        raise ImportError(
            "FastAPI is required for the web API. Install fastapi and uvicorn in the AI-Linux-Assistant environment."
        )


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=120)


class ProjectCreateRequest(BaseModel):
    user_id: str
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""


class ProjectUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""


class ChatCreateRequest(BaseModel):
    title: str = ""


class ChatUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class RunCreateRequest(BaseModel):
    content: str = Field(..., min_length=1)
    magi: str = "off"
    client_request_id: str = ""


class MessageCreateRequest(RunCreateRequest):
    pass


class UserResponse(BaseModel):
    id: str
    username: str


class ProjectResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str
    created_at: str
    updated_at: str


class ChatRunResponse(BaseModel):
    id: str
    chat_session_id: str
    project_id: str
    user_id: str
    status: str
    request_content: str
    magi: str
    client_request_id: str
    latest_state_code: str
    latest_event_seq: int
    partial_assistant_text: str
    error_message: str
    worker_id: str
    cancel_requested: bool
    lease_expires_at: str
    started_at: str
    finished_at: str
    created_at: str
    updated_at: str
    final_user_message_id: int | None = None
    final_assistant_message_id: int | None = None


class ChatSessionResponse(BaseModel):
    id: str
    project_id: str
    title: str
    created_at: str
    updated_at: str
    active_run_id: str | None = None
    active_run_status: str | None = None


class ChatMessageResponse(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    created_at: str
    council_entries: list | None = None


class AssistantDebugResponse(BaseModel):
    state_trace: list[str]
    tool_events: list[dict[str, Any]]
    retrieval_query: str
    retrieved_sources: list[str]


class SendMessageResponse(BaseModel):
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
    debug: AssistantDebugResponse


class BootstrapResponse(BaseModel):
    user: UserResponse
    projects: list[ProjectResponse]
    chats_by_project: dict[str, list[ChatSessionResponse]]


def _iso(value):
    return value.isoformat() if value is not None else ""


def _serialize_user(user):
    return UserResponse(id=user.id, username=user.username)


def _serialize_project(project):
    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        name=project.name,
        description=project.description or "",
        created_at=_iso(getattr(project, "created_at", None)),
        updated_at=_iso(getattr(project, "updated_at", None)),
    )


def _serialize_chat_session(chat_session, active_run=None):
    return ChatSessionResponse(
        id=chat_session.id,
        project_id=chat_session.project_id,
        title=(chat_session.title or "").strip(),
        created_at=_iso(getattr(chat_session, "created_at", None)),
        updated_at=_iso(getattr(chat_session, "updated_at", None)),
        active_run_id=getattr(active_run, "id", None),
        active_run_status=getattr(active_run, "status", None),
    )


def _serialize_message(message):
    return ChatMessageResponse(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        created_at=_iso(getattr(message, "created_at", None)),
        council_entries=getattr(message, "council_entries", None) or None,
    )


def _serialize_run(run):
    return ChatRunResponse(
        id=run.id,
        chat_session_id=run.chat_session_id,
        project_id=run.project_id,
        user_id=run.user_id,
        status=run.status,
        request_content=run.request_content or "",
        magi=run.magi or "off",
        client_request_id=run.client_request_id or "",
        latest_state_code=run.latest_state_code or "",
        latest_event_seq=int(run.latest_event_seq or 0),
        partial_assistant_text=run.partial_assistant_text or "",
        error_message=run.error_message or "",
        worker_id=run.worker_id or "",
        cancel_requested=bool(run.cancel_requested),
        lease_expires_at=_iso(getattr(run, "lease_expires_at", None)),
        started_at=_iso(getattr(run, "started_at", None)),
        finished_at=_iso(getattr(run, "finished_at", None)),
        created_at=_iso(getattr(run, "created_at", None)),
        updated_at=_iso(getattr(run, "updated_at", None)),
        final_user_message_id=getattr(run, "final_user_message_id", None),
        final_assistant_message_id=getattr(run, "final_assistant_message_id", None),
    )


def _extract_sources(retrieved_docs):
    sources = []
    for line in (retrieved_docs or "").splitlines():
        if line.startswith("[Source:"):
            sources.append(line.strip())
    return sources


def _sse_payload(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _model_dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _client_request_id(raw_value: str | None):
    value = (raw_value or "").strip()
    return value or uuid.uuid4().hex


def _serialize_event_row(event_row):
    payload = event_row.payload_json or {}
    if event_row.type == "state":
        return {"type": "state", "seq": event_row.seq, "code": event_row.code}
    if event_row.type == "event":
        return {"type": "event", "seq": event_row.seq, "code": event_row.code, "payload": payload}
    if event_row.type == "done":
        return {"type": "done", "seq": event_row.seq, **payload}
    if event_row.type == "error":
        return {"type": "error", "seq": event_row.seq, "message": payload.get("message", "")}
    if event_row.type == "cancelled":
        return {"type": "cancelled", "seq": event_row.seq, "message": payload.get("message", "Run cancelled.")}
    return {"type": event_row.type, "seq": event_row.seq, "code": event_row.code, "payload": payload}


def _terminal_event_from_snapshot(run, app_store):
    if run.status == "completed" and run.final_user_message_id and run.final_assistant_message_id:
        user_message = app_store.get_message(run.final_user_message_id)
        assistant_message = app_store.get_message(run.final_assistant_message_id)
        if user_message is None or assistant_message is None:
            return {"type": "error", "message": "Run completed without persisted messages."}
        return {
            "type": "done",
            "user_message": _model_dump(_serialize_message(user_message)),
            "assistant_message": _model_dump(_serialize_message(assistant_message)),
            "debug": {
                "state_trace": [],
                "tool_events": [],
                "retrieval_query": "",
                "retrieved_sources": [],
            },
        }
    if run.status == "cancelled":
        return {"type": "cancelled", "message": run.error_message or "Run cancelled."}
    if run.status == "failed":
        return {"type": "error", "message": run.error_message or "Run failed."}
    return None


def _wait_for_terminal_run(run_store, run_id, timeout_seconds=1800):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        run = run_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        time.sleep(0.25)
    raise HTTPException(status_code=504, detail="Timed out waiting for run completion.")


def create_app():
    _require_fastapi()
    app = FastAPI(title="AI Linux Assistant API")
    app_store = PostgresAppStore()
    run_store = PostgresRunStore()

    if CORSMiddleware is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _chat_context_or_404(chat_session_id):
        context = app_store.get_session_context(chat_session_id)
        if context is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        return context

    def _create_or_reuse_run(chat_session_id, request):
        context = _chat_context_or_404(chat_session_id)
        try:
            return run_store.create_or_reuse_run(
                chat_session_id=chat_session_id,
                project_id=context["project_id"],
                user_id=context["user_id"],
                request_content=request.content,
                magi=request.magi,
                client_request_id=_client_request_id(request.client_request_id),
                max_active_runs_per_user=SETTINGS.max_active_runs_per_user_default,
            )
        except ActiveChatRunExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ActiveRunLimitExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    def _run_result_or_error(run):
        if run.status == "completed":
            if not run.final_user_message_id or not run.final_assistant_message_id:
                raise HTTPException(status_code=500, detail="Run completed without final messages.")
            user_message = app_store.get_message(run.final_user_message_id)
            assistant_message = app_store.get_message(run.final_assistant_message_id)
            if user_message is None or assistant_message is None:
                raise HTTPException(status_code=500, detail="Run completed without persisted messages.")
            done_debug = {"state_trace": [], "tool_events": [], "retrieval_query": "", "retrieved_sources": []}
            for event_row in run_store.list_events_after(run.id, after_seq=0, limit=max(1, int(run.latest_event_seq or 0) + 5)):
                if event_row.type == "done" and isinstance(event_row.payload_json, dict):
                    done_debug = event_row.payload_json.get("debug") or done_debug
            return SendMessageResponse(
                user_message=_serialize_message(user_message),
                assistant_message=_serialize_message(assistant_message),
                debug=AssistantDebugResponse(
                    state_trace=list(done_debug.get("state_trace", []) or []),
                    tool_events=list(done_debug.get("tool_events", []) or []),
                    retrieval_query=done_debug.get("retrieval_query", "") or "",
                    retrieved_sources=list(done_debug.get("retrieved_sources", []) or []),
                ),
            )
        if run.status == "cancelled":
            raise HTTPException(status_code=409, detail=run.error_message or "Run cancelled.")
        raise HTTPException(status_code=500, detail=run.error_message or "Run failed.")

    def _chat_responses_for_rows(chat_rows):
        active_by_chat = run_store.get_active_runs_for_chat_ids([chat.id for chat in chat_rows])
        return [_serialize_chat_session(chat, active_run=active_by_chat.get(chat.id)) for chat in chat_rows]

    def _stream_run(run_id, after_seq=0):
        poll_seconds = max(0.1, float(SETTINGS.chat_run_stream_poll_ms) / 1000.0)

        def stream():
            current_seq = max(0, int(after_seq))
            terminal_sent = False
            while True:
                events = run_store.list_events_after(run_id, after_seq=current_seq)
                for event_row in events:
                    current_seq = max(current_seq, int(event_row.seq))
                    payload = _serialize_event_row(event_row)
                    yield _sse_payload(payload)
                    if payload["type"] in {"done", "error", "cancelled"}:
                        terminal_sent = True
                if terminal_sent:
                    break

                run = run_store.get_run(run_id)
                if run is None:
                    yield _sse_payload({"type": "error", "message": "Run not found."})
                    break
                if run.status in TERMINAL_RUN_STATUSES:
                    fallback_payload = _terminal_event_from_snapshot(run, app_store)
                    if fallback_payload is not None:
                        yield _sse_payload(fallback_payload)
                    break
                time.sleep(poll_seconds)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/auth/login", response_model=UserResponse)
    def login(request: LoginRequest):
        user = app_store.find_or_create_user(request.username)
        return _serialize_user(user)

    @app.post("/auth/bootstrap", response_model=BootstrapResponse)
    def bootstrap(request: LoginRequest):
        result = app_store.bootstrap_user(request.username)
        all_chat_ids = [chat["id"] for chats in result["chats_by_project"].values() for chat in chats]
        active_by_chat = run_store.get_active_runs_for_chat_ids(all_chat_ids)
        return BootstrapResponse(
            user=UserResponse(id=result["user_id"], username=result["user_username"]),
            projects=[
                ProjectResponse(
                    id=p["id"],
                    user_id=p["user_id"],
                    name=p["name"],
                    description=p["description"],
                    created_at=_iso(p["created_at"]),
                    updated_at=_iso(p["updated_at"]),
                )
                for p in result["projects"]
            ],
            chats_by_project={
                project_id: [
                    ChatSessionResponse(
                        id=c["id"],
                        project_id=c["project_id"],
                        title=c["title"],
                        created_at=_iso(c["created_at"]),
                        updated_at=_iso(c["updated_at"]),
                        active_run_id=getattr(active_by_chat.get(c["id"]), "id", None),
                        active_run_status=getattr(active_by_chat.get(c["id"]), "status", None),
                    )
                    for c in chats
                ]
                for project_id, chats in result["chats_by_project"].items()
            },
        )

    @app.get("/users/{user_id}/projects", response_model=list[ProjectResponse])
    def list_projects(user_id: str):
        return [_serialize_project(project) for project in app_store.list_projects(user_id)]

    @app.post("/projects", response_model=ProjectResponse)
    def create_project(request: ProjectCreateRequest):
        project = app_store.create_project(request.user_id, request.name, description=request.description)
        return _serialize_project(project)

    @app.get("/projects/{project_id}", response_model=ProjectResponse)
    def get_project(project_id: str, user_id: str | None = None):
        project = app_store.get_project(project_id, user_id=user_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return _serialize_project(project)

    @app.patch("/projects/{project_id}", response_model=ProjectResponse)
    def update_project(project_id: str, request: ProjectUpdateRequest):
        project = app_store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        try:
            updated_project = app_store.update_project(project_id, request.name, description=request.description)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _serialize_project(updated_project)

    @app.delete("/projects/{project_id}")
    def delete_project(project_id: str):
        project = app_store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        try:
            app_store.delete_project(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/projects/{project_id}/chats", response_model=list[ChatSessionResponse])
    def list_chats(project_id: str):
        chats = app_store.list_chat_sessions_checked(project_id)
        if chats is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return _chat_responses_for_rows(chats)

    @app.post("/projects/{project_id}/chats", response_model=ChatSessionResponse)
    def create_chat(project_id: str, request: ChatCreateRequest):
        project = app_store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        chat_session = app_store.create_chat_session(project_id, title=request.title)
        return _serialize_chat_session(chat_session)

    @app.get("/chats/{chat_session_id}", response_model=ChatSessionResponse)
    def get_chat(chat_session_id: str):
        chat_session = app_store.get_chat_session(chat_session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        active_run = run_store.get_active_run_for_chat(chat_session_id)
        return _serialize_chat_session(chat_session, active_run=active_run)

    @app.patch("/chats/{chat_session_id}", response_model=ChatSessionResponse)
    def update_chat(chat_session_id: str, request: ChatUpdateRequest):
        chat_session = app_store.get_chat_session(chat_session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        try:
            updated_chat = app_store.update_chat_session_title(chat_session_id, request.title)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        active_run = run_store.get_active_run_for_chat(chat_session_id)
        return _serialize_chat_session(updated_chat, active_run=active_run)

    @app.delete("/chats/{chat_session_id}")
    def delete_chat(chat_session_id: str):
        chat_session = app_store.get_chat_session(chat_session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        try:
            app_store.delete_chat_session(chat_session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/chats/{chat_session_id}/messages", response_model=list[ChatMessageResponse])
    def list_messages(chat_session_id: str):
        messages = app_store.list_messages_checked(chat_session_id)
        if messages is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        return [_serialize_message(message) for message in messages]

    @app.post("/chats/{chat_session_id}/runs", response_model=ChatRunResponse)
    def create_run(chat_session_id: str, request: RunCreateRequest):
        run = _create_or_reuse_run(chat_session_id, request)
        return _serialize_run(run)

    @app.get("/runs/{run_id}", response_model=ChatRunResponse)
    def get_run(run_id: str):
        run = run_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return _serialize_run(run)

    @app.get("/runs/{run_id}/events")
    def list_run_events(run_id: str, after_seq: int = 0):
        run = run_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        events = run_store.list_events_after(run_id, after_seq=after_seq)
        return [_serialize_event_row(event) for event in events]

    @app.get("/runs/{run_id}/events/stream")
    def stream_run_events(run_id: str, after_seq: int = 0):
        run = run_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return _stream_run(run_id, after_seq=after_seq)

    @app.post("/runs/{run_id}/cancel", response_model=ChatRunResponse)
    def cancel_run(run_id: str):
        try:
            run = run_store.request_cancel(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _serialize_run(run)

    @app.post("/runs/{run_id}/fail", response_model=ChatRunResponse)
    def fail_run(run_id: str):
        try:
            run = run_store.mark_failed(run_id, error_message="Run marked failed by operator.")
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _serialize_run(run)

    @app.post("/runs/{run_id}/requeue", response_model=ChatRunResponse)
    def requeue_run(run_id: str):
        try:
            run = run_store.requeue_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RunRequeueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _serialize_run(run)

    @app.post("/chats/{chat_session_id}/messages", response_model=SendMessageResponse)
    def send_message(chat_session_id: str, request: MessageCreateRequest):
        run = _create_or_reuse_run(chat_session_id, request)
        terminal_run = _wait_for_terminal_run(run_store, run.id)
        return _run_result_or_error(terminal_run)

    @app.post("/chats/{chat_session_id}/messages/stream")
    def send_message_stream(chat_session_id: str, request: MessageCreateRequest):
        run = _create_or_reuse_run(chat_session_id, request)
        return _stream_run(run.id, after_seq=0)

    return app


app = create_app() if FastAPI is not None else None
