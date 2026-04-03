import json
import time
import uuid
from typing import Any

from auth import Auth0AccessTokenVerifier, AuthConfigurationError, build_current_user_dependency
from config.settings import load_settings
from persistence.postgres_app_store import PostgresAppStore
from persistence.postgres_run_store import (
    ActiveChatRunExistsError,
    ActiveRunLimitExceededError,
    PAUSE_REQUESTABLE_MAGI_STATES,
    PostgresRunStore,
    RunNotFoundError,
    RunRequeueError,
    RunStateConflictError,
    TERMINAL_RUN_STATUSES,
)
from streaming.replay_filters import (
    register_checkpoint_window as _register_checkpoint_window,
    should_forward_stream_delta as _should_forward_stream_delta,
)
from streaming.event_serializer import serialize_run_event
from streaming.redis_events import get_shared_client as _get_redis_client

try:
    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - optional until FastAPI is installed
    Depends = None
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


class RunResumeRequest(BaseModel):
    input_text: str = ""
    input_kind: str = "fact"


class UserResponse(BaseModel):
    id: str
    username: str | None = None
    display_name: str = ""
    email: str = ""
    email_verified: bool = False
    avatar_url: str = ""
    role: str


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
    run_kind: str
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


class ChatRunListResponse(BaseModel):
    runs: list[ChatRunResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


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


class AppBootstrapResponse(BaseModel):
    user: UserResponse
    projects: list[ProjectResponse]
    chats_by_project: dict[str, list[ChatSessionResponse]]


def _iso(value):
    return value.isoformat() if value is not None else ""


def _serialize_user(user):
    return UserResponse(
        id=user.id,
        username=getattr(user, "username", None),
        display_name=getattr(user, "display_name", "") or "",
        email=getattr(user, "email", "") or "",
        email_verified=bool(getattr(user, "email_verified", False)),
        avatar_url=getattr(user, "avatar_url", "") or "",
        role=user.role,
    )


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
        run_kind=getattr(run, "run_kind", "message") or "message",
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
    return serialize_run_event(
        event_row.seq,
        event_row.type,
        event_row.code,
        event_row.payload_json,
        created_at=getattr(event_row, "created_at", None),
    )


def _degraded_terminal_event_from_snapshot(run, app_store):
    terminal_created_at = _iso(getattr(run, "finished_at", None) or getattr(run, "updated_at", None))
    if run.status == "completed" and run.final_user_message_id and run.final_assistant_message_id:
        user_message = app_store.get_message(run.final_user_message_id)
        assistant_message = app_store.get_message(run.final_assistant_message_id)
        if user_message is None or assistant_message is None:
            return {"type": "error", "message": "Run completed without persisted messages.", "created_at": terminal_created_at}
        return {
            "type": "done",
            "created_at": terminal_created_at,
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
        return {"type": "cancelled", "message": run.error_message or "Run cancelled.", "created_at": terminal_created_at}
    if run.status == "failed":
        return {"type": "error", "message": run.error_message or "Run failed.", "created_at": terminal_created_at}
    return None


def _degraded_paused_event_from_snapshot(run):
    created_at = _iso(getattr(run, "updated_at", None))
    payload = {
        "message": "Run paused.",
        "pause_state": getattr(run, "pause_state_json", None) or None,
    }
    return {
        "type": "paused",
        "seq": int(getattr(run, "latest_event_seq", 0) or 0),
        "message": payload["message"],
        "created_at": created_at,
        "payload": payload,
    }


def _terminal_event_payload(run_store, run_id, run, app_store):
    event_row = run_store.get_terminal_event(run_id)
    if event_row is not None:
        return _serialize_event_row(event_row)
    return _degraded_terminal_event_from_snapshot(run, app_store)


def _stream_stop_payload(run_store, run_id, run, app_store):
    if run.status == "paused":
        event_row = run_store.get_latest_event_by_code(run_id, "paused")
        if event_row is not None:
            return _serialize_event_row(event_row)
        return _degraded_paused_event_from_snapshot(run)
    return _terminal_event_payload(run_store, run_id, run, app_store)


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


def _auth0_required(settings) -> bool:
    return bool(settings.auth0_enabled) and not bool(settings.enable_legacy_bootstrap_auth)


def _has_complete_auth0_config(settings) -> bool:
    return bool((settings.auth0_domain or "").strip() and (settings.auth0_issuer or "").strip() and (settings.auth0_audience or "").strip())


def _default_auth0_verifier(settings):
    return Auth0AccessTokenVerifier(
        domain=settings.auth0_domain,
        issuer=settings.auth0_issuer,
        audience=settings.auth0_audience,
    )


def create_app(*, app_store=None, run_store=None, auth_verifier=None):
    _require_fastapi()
    settings = load_settings()
    auth_required = _auth0_required(settings) or (auth_verifier is not None)
    if auth_required and auth_verifier is None and not _has_complete_auth0_config(settings):
        raise AuthConfigurationError(
            "Auth0 web auth is enabled but AUTH0_DOMAIN, AUTH0_ISSUER, and AUTH0_AUDIENCE are not fully configured."
        )
    if auth_required and auth_verifier is None and not tuple(settings.frontend_origins or ()):
        raise AuthConfigurationError(
            "Auth0 web auth is enabled but FRONTEND_ORIGIN/FRONTEND_ORIGINS is not configured."
        )
    app = FastAPI(title="AI Linux Assistant API")
    app_store = app_store or PostgresAppStore()
    run_store = run_store or PostgresRunStore(redis_client=_get_redis_client())
    verifier = auth_verifier or (_default_auth0_verifier(settings) if _auth0_required(settings) else None)
    current_user_dependency = build_current_user_dependency(app_store, verifier) if verifier is not None else None

    if CORSMiddleware is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.frontend_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    def _auth_unconfigured():
        if verifier is None:
            raise HTTPException(status_code=503, detail="Web authentication is not configured.")
        raise HTTPException(status_code=500, detail="Authentication dependency was not initialized.")

    def _require_current_user(current_user=Depends(current_user_dependency or _auth_unconfigured)):
        return current_user

    def _require_admin(current_user=Depends(_require_current_user)):
        if (getattr(current_user, "role", "") or "") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required.")
        return current_user

    def _chat_context_or_404(chat_session_id, user_id):
        context = app_store.get_session_context_for_user(chat_session_id, user_id)
        if context is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        return context

    def _create_or_reuse_run(chat_session_id, request, user_id):
        context = _chat_context_or_404(chat_session_id, user_id)
        try:
            return run_store.create_or_reuse_run(
                chat_session_id=chat_session_id,
                project_id=context["project_id"],
                user_id=context["user_id"],
                request_content=request.content,
                magi=request.magi,
                client_request_id=_client_request_id(request.client_request_id),
                max_active_runs_per_user=settings.max_active_runs_per_user_default,
            )
        except ActiveChatRunExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ActiveRunLimitExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    def _run_result_or_error(run):
        terminal_payload = _terminal_event_payload(run_store, run.id, run, app_store)
        if run.status == "completed":
            if terminal_payload is None or terminal_payload.get("type") != "done":
                raise HTTPException(status_code=500, detail="Run completed without a durable terminal payload.")
            return SendMessageResponse(
                user_message=terminal_payload["user_message"],
                assistant_message=terminal_payload["assistant_message"],
                debug=AssistantDebugResponse(
                    state_trace=list((terminal_payload.get("debug") or {}).get("state_trace", []) or []),
                    tool_events=list((terminal_payload.get("debug") or {}).get("tool_events", []) or []),
                    retrieval_query=((terminal_payload.get("debug") or {}).get("retrieval_query", "") or ""),
                    retrieved_sources=list((terminal_payload.get("debug") or {}).get("retrieved_sources", []) or []),
                ),
            )
        if run.status == "cancelled":
            message = (terminal_payload or {}).get("message") or run.error_message or "Run cancelled."
            raise HTTPException(status_code=409, detail=message)
        message = (terminal_payload or {}).get("message") or run.error_message or "Run failed."
        raise HTTPException(status_code=500, detail=message)

    def _cancel_run_explicitly(run_id: str, user_id: str):
        for _attempt in range(3):
            run = run_store.get_run_for_user(run_id, user_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found.")
            if run.status in TERMINAL_RUN_STATUSES:
                return run
            try:
                if run.status == "queued":
                    return run_store.cancel_queued_run(run_id, error_message="Run cancelled.")
                if run.status == "paused":
                    return run_store.mark_cancelled(run_id, error_message="Run cancelled.")
                if run.status in {"running", "cancel_requested"}:
                    return run_store.request_running_cancel(run_id)
                if run.status == "pause_requested":
                    return run_store.request_running_cancel(run_id)
                raise HTTPException(status_code=409, detail=f"Run '{run_id}' cannot be cancelled from status '{run.status}'.")
            except RunStateConflictError:
                continue

        run = run_store.get_run_for_user(run_id, user_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        if run.status in TERMINAL_RUN_STATUSES | {"running", "cancel_requested", "pause_requested", "paused"}:
            return run
        raise HTTPException(status_code=409, detail=f"Run '{run_id}' cannot be cancelled from status '{run.status}'.")

    def _pause_run_explicitly(run_id: str, user_id: str):
        run = run_store.get_run_for_user(run_id, user_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        if (run.run_kind or "message") != "message" or (run.magi or "off") not in {"full", "lite"}:
            raise HTTPException(status_code=409, detail="Only active MAGI message runs can be paused.")
        if run.status in {"pause_requested", "paused"}:
            return run
        if run.status != "running":
            raise HTTPException(status_code=409, detail=f"Run '{run_id}' cannot be paused from status '{run.status}'.")
        latest_magi_state = run_store.get_latest_event_by_code(run_id, "magi_state")
        latest_state_name = str(((latest_magi_state.payload_json or {}).get("state") if latest_magi_state else "") or "")
        if latest_state_name not in PAUSE_REQUESTABLE_MAGI_STATES:
            raise HTTPException(status_code=409, detail="MAGI can only be paused during opening arguments or discussion.")
        try:
            return run_store.request_pause(run_id)
        except RunStateConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _resume_run_explicitly(run_id: str, request: RunResumeRequest, user_id: str):
        if run_store.get_run_for_user(run_id, user_id) is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        try:
            return run_store.resume_paused_run(
                run_id,
                input_text=request.input_text,
                input_kind=request.input_kind,
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RunStateConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _chat_responses_for_rows(chat_rows, user_id):
        active_by_chat = run_store.get_active_runs_for_chat_ids([chat.id for chat in chat_rows], user_id=user_id)
        return [_serialize_chat_session(chat, active_run=active_by_chat.get(chat.id)) for chat in chat_rows]

    def _stream_via_redis(run_id, after_seq, redis_client):
        """Subscribe to the Redis fanout channel, replay Postgres backlog first, then
        forward live Redis messages. Subscribe happens BEFORE the backlog query so no
        event can fall into the gap between the two.  Messages already covered by the
        backlog are skipped by seq comparison."""
        from streaming.redis_events import subscribe_run_events
        health_poll = max(0.5, float(settings.chat_run_stream_poll_ms) / 1000.0)
        ps = subscribe_run_events(redis_client, run_id)
        try:
            current_seq = max(0, int(after_seq))
            checkpoint_windows = {}
            # Replay backlog
            for event_row in run_store.list_events_after(run_id, after_seq=current_seq, limit=1000):
                current_seq = max(current_seq, int(event_row.seq))
                payload = _serialize_event_row(event_row)
                _register_checkpoint_window(checkpoint_windows, payload)
                yield _sse_payload(payload)
                if payload["type"] in {"done", "error", "cancelled", "paused"}:
                    return
            # Forward Redis messages
            while True:
                msg = ps.get_message(timeout=health_poll)
                if msg is None:
                    run = run_store.get_run(run_id)
                    if run is None:
                        yield _sse_payload({"type": "error", "message": "Run not found."})
                        return
                    if run.status in TERMINAL_RUN_STATUSES | {"paused"}:
                        fallback_payload = _stream_stop_payload(run_store, run_id, run, app_store)
                        if fallback_payload is not None:
                            yield _sse_payload(fallback_payload)
                        return
                    continue
                if msg["type"] != "message":
                    continue
                data = json.loads(msg["data"])
                seq = int(data.get("seq", 0) or 0)
                if seq > 0 and seq <= current_seq:
                    continue  # already sent via backlog
                if data.get("type") == "event" and data.get("code") in {"text_delta", "magi_role_text_delta"}:
                    if not _should_forward_stream_delta(data, checkpoint_windows):
                        continue
                if seq > 0:
                    current_seq = seq
                _register_checkpoint_window(checkpoint_windows, data)
                yield _sse_payload(data)
                if data.get("type") in {"done", "error", "cancelled", "paused"}:
                    return
        finally:
            ps.unsubscribe()
            ps.close()

    def _stream_run(run_id, after_seq=0):
        poll_seconds = max(0.1, float(settings.chat_run_stream_poll_ms) / 1000.0)
        redis_client = _get_redis_client()

        def stream():
            if redis_client is not None:
                try:
                    yield from _stream_via_redis(run_id, after_seq, redis_client)
                    return
                except Exception:
                    pass  # fall through to Postgres polling

            current_seq = max(0, int(after_seq))
            terminal_sent = False
            while True:
                events = run_store.list_events_after(run_id, after_seq=current_seq)
                for event_row in events:
                    current_seq = max(current_seq, int(event_row.seq))
                    payload = _serialize_event_row(event_row)
                    yield _sse_payload(payload)
                    if payload["type"] in {"done", "error", "cancelled", "paused"}:
                        terminal_sent = True
                if terminal_sent:
                    break

                run = run_store.get_run(run_id)
                if run is None:
                    yield _sse_payload({"type": "error", "message": "Run not found."})
                    break
                if run.status in TERMINAL_RUN_STATUSES | {"paused"}:
                    fallback_payload = _stream_stop_payload(run_store, run_id, run, app_store)
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

    if settings.enable_legacy_bootstrap_auth:
        @app.post("/auth/login", response_model=UserResponse)
        def login(request: LoginRequest):
            user = app_store.find_or_create_user(request.username)
            return _serialize_user(user)

        @app.post("/auth/bootstrap", response_model=AppBootstrapResponse)
        def bootstrap(request: LoginRequest):
            result = app_store.bootstrap_user(request.username)
            all_chat_ids = [chat["id"] for chats in result["chats_by_project"].values() for chat in chats]
            active_by_chat = run_store.get_active_runs_for_chat_ids(all_chat_ids, user_id=result["user_id"])
            return AppBootstrapResponse(
                user=_serialize_user(app_store.get_user_by_id(result["user_id"])),
                projects=[
                    ProjectResponse(
                        id=project["id"],
                        user_id=project["user_id"],
                        name=project["name"],
                        description=project["description"],
                        created_at=_iso(project["created_at"]),
                        updated_at=_iso(project["updated_at"]),
                    )
                    for project in result["projects"]
                ],
                chats_by_project={
                    project_id: [
                        ChatSessionResponse(
                            id=chat["id"],
                            project_id=chat["project_id"],
                            title=chat["title"],
                            created_at=_iso(chat["created_at"]),
                            updated_at=_iso(chat["updated_at"]),
                            active_run_id=getattr(active_by_chat.get(chat["id"]), "id", None),
                            active_run_status=getattr(active_by_chat.get(chat["id"]), "status", None),
                        )
                        for chat in chats
                    ]
                    for project_id, chats in result["chats_by_project"].items()
                },
            )

    @app.get("/auth/me", response_model=UserResponse)
    def auth_me(current_user=Depends(_require_current_user)):
        return _serialize_user(current_user)

    @app.get("/app/bootstrap", response_model=AppBootstrapResponse)
    def app_bootstrap(current_user=Depends(_require_current_user)):
        result = app_store.bootstrap_app_session(current_user.id)
        if result is None:
            raise HTTPException(status_code=404, detail="User not found.")
        all_chat_ids = [chat["id"] for chats in result["chats_by_project"].values() for chat in chats]
        active_by_chat = run_store.get_active_runs_for_chat_ids(all_chat_ids, user_id=current_user.id)
        return AppBootstrapResponse(
            user=_serialize_user(result["user"]),
            projects=[
                ProjectResponse(
                    id=project["id"],
                    user_id=project["user_id"],
                    name=project["name"],
                    description=project["description"],
                    created_at=_iso(project["created_at"]),
                    updated_at=_iso(project["updated_at"]),
                )
                for project in result["projects"]
            ],
            chats_by_project={
                project_id: [
                    ChatSessionResponse(
                        id=chat["id"],
                        project_id=chat["project_id"],
                        title=chat["title"],
                        created_at=_iso(chat["created_at"]),
                        updated_at=_iso(chat["updated_at"]),
                        active_run_id=getattr(active_by_chat.get(chat["id"]), "id", None),
                        active_run_status=getattr(active_by_chat.get(chat["id"]), "status", None),
                    )
                    for chat in chats
                ]
                for project_id, chats in result["chats_by_project"].items()
            },
        )

    @app.get("/projects", response_model=list[ProjectResponse])
    def list_projects(current_user=Depends(_require_current_user)):
        return [_serialize_project(project) for project in app_store.list_projects_for_user(current_user.id)]

    @app.post("/projects", response_model=ProjectResponse)
    def create_project(request: ProjectCreateRequest, current_user=Depends(_require_current_user)):
        project = app_store.create_project(current_user.id, request.name, description=request.description)
        return _serialize_project(project)

    @app.get("/projects/{project_id}", response_model=ProjectResponse)
    def get_project(project_id: str, current_user=Depends(_require_current_user)):
        project = app_store.get_project_for_user(project_id, current_user.id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return _serialize_project(project)

    @app.patch("/projects/{project_id}", response_model=ProjectResponse)
    def update_project(project_id: str, request: ProjectUpdateRequest, current_user=Depends(_require_current_user)):
        try:
            updated_project = app_store.update_project_for_user(project_id, current_user.id, request.name, description=request.description)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _serialize_project(updated_project)

    @app.delete("/projects/{project_id}")
    def delete_project(project_id: str, current_user=Depends(_require_current_user)):
        try:
            app_store.delete_project_for_user(project_id, current_user.id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/projects/{project_id}/chats", response_model=list[ChatSessionResponse])
    def list_chats(project_id: str, current_user=Depends(_require_current_user)):
        chats = app_store.list_chat_sessions_for_user_project(current_user.id, project_id)
        if chats is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return _chat_responses_for_rows(chats, current_user.id)

    @app.post("/projects/{project_id}/chats", response_model=ChatSessionResponse)
    def create_chat(project_id: str, request: ChatCreateRequest, current_user=Depends(_require_current_user)):
        try:
            chat_session = app_store.create_chat_session_for_user(project_id, current_user.id, title=request.title)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _serialize_chat_session(chat_session)

    @app.get("/chats/{chat_session_id}", response_model=ChatSessionResponse)
    def get_chat(chat_session_id: str, current_user=Depends(_require_current_user)):
        chat_session = app_store.get_chat_session_for_user(chat_session_id, current_user.id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        active_run = run_store.get_active_run_for_chat_for_user(chat_session_id, current_user.id)
        return _serialize_chat_session(chat_session, active_run=active_run)

    @app.patch("/chats/{chat_session_id}", response_model=ChatSessionResponse)
    def update_chat(chat_session_id: str, request: ChatUpdateRequest, current_user=Depends(_require_current_user)):
        try:
            updated_chat = app_store.update_chat_session_title_for_user(chat_session_id, current_user.id, request.title)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        active_run = run_store.get_active_run_for_chat_for_user(chat_session_id, current_user.id)
        return _serialize_chat_session(updated_chat, active_run=active_run)

    @app.delete("/chats/{chat_session_id}")
    def delete_chat(chat_session_id: str, current_user=Depends(_require_current_user)):
        try:
            app_store.delete_chat_session_for_user(chat_session_id, current_user.id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/chats/{chat_session_id}/messages", response_model=list[ChatMessageResponse])
    def list_messages(chat_session_id: str, current_user=Depends(_require_current_user)):
        messages = app_store.list_messages_for_user_chat(current_user.id, chat_session_id)
        if messages is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        return [_serialize_message(message) for message in messages]

    @app.get("/chats/{chat_session_id}/runs", response_model=ChatRunListResponse)
    def list_chat_runs(
        chat_session_id: str,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        current_user=Depends(_require_current_user),
    ):
        _chat_context_or_404(chat_session_id, current_user.id)
        normalized_page = max(1, int(page))
        normalized_page_size = min(100, max(1, int(page_size)))
        runs, total = run_store.list_runs_for_chat_for_user(
            chat_session_id,
            current_user.id,
            page=normalized_page,
            page_size=normalized_page_size,
            status=status,
        )
        return ChatRunListResponse(
            runs=[_serialize_run(run) for run in runs],
            total=total,
            page=normalized_page,
            page_size=normalized_page_size,
            has_more=(normalized_page * normalized_page_size) < total,
        )

    @app.post("/chats/{chat_session_id}/runs", response_model=ChatRunResponse)
    def create_run(chat_session_id: str, request: RunCreateRequest, current_user=Depends(_require_current_user)):
        run = _create_or_reuse_run(chat_session_id, request, current_user.id)
        return _serialize_run(run)

    @app.get("/runs/{run_id}", response_model=ChatRunResponse)
    def get_run(run_id: str, current_user=Depends(_require_current_user)):
        run = run_store.get_run_for_user(run_id, current_user.id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return _serialize_run(run)

    @app.get("/runs/{run_id}/events")
    def list_run_events(run_id: str, after_seq: int = 0, limit: int = 200, current_user=Depends(_require_current_user)):
        run = run_store.get_run_for_user(run_id, current_user.id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        normalized_limit = min(1000, max(1, int(limit)))
        events = run_store.list_events_after(run_id, after_seq=after_seq, limit=normalized_limit)
        return [_serialize_event_row(event) for event in events]

    @app.get("/runs/{run_id}/events/stream")
    def stream_run_events(run_id: str, after_seq: int = 0, current_user=Depends(_require_current_user)):
        run = run_store.get_run_for_user(run_id, current_user.id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return _stream_run(run_id, after_seq=after_seq)

    @app.post("/runs/{run_id}/cancel", response_model=ChatRunResponse)
    def cancel_run(run_id: str, current_user=Depends(_require_current_user)):
        try:
            run = _cancel_run_explicitly(run_id, current_user.id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _serialize_run(run)

    @app.post("/runs/{run_id}/pause", response_model=ChatRunResponse)
    def pause_run(run_id: str, current_user=Depends(_require_current_user)):
        return _serialize_run(_pause_run_explicitly(run_id, current_user.id))

    @app.post("/runs/{run_id}/resume", response_model=ChatRunResponse)
    def resume_run(run_id: str, request: RunResumeRequest, current_user=Depends(_require_current_user)):
        return _serialize_run(_resume_run_explicitly(run_id, request, current_user.id))

    @app.post("/runs/{run_id}/fail", response_model=ChatRunResponse)
    def fail_run(run_id: str, _admin=Depends(_require_admin)):
        try:
            run = run_store.mark_failed(run_id, error_message="Run marked failed by operator.")
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _serialize_run(run)

    @app.post("/runs/{run_id}/requeue", response_model=ChatRunResponse)
    def requeue_run(run_id: str, _admin=Depends(_require_admin)):
        try:
            run = run_store.requeue_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RunRequeueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _serialize_run(run)

    @app.post("/chats/{chat_session_id}/messages", response_model=SendMessageResponse)
    def send_message(chat_session_id: str, request: MessageCreateRequest, current_user=Depends(_require_current_user)):
        run = _create_or_reuse_run(chat_session_id, request, current_user.id)
        terminal_run = _wait_for_terminal_run(run_store, run.id)
        return _run_result_or_error(terminal_run)

    @app.post("/chats/{chat_session_id}/messages/stream")
    def send_message_stream(chat_session_id: str, request: MessageCreateRequest, current_user=Depends(_require_current_user)):
        run = _create_or_reuse_run(chat_session_id, request, current_user.id)
        return _stream_run(run.id, after_seq=0)

    return app


def _can_create_default_app() -> bool:
    if FastAPI is None:
        return False
    settings = load_settings()
    if settings.enable_legacy_bootstrap_auth:
        return True
    if not settings.auth0_enabled:
        return False
    return _has_complete_auth0_config(settings) and bool(tuple(settings.frontend_origins or ()))


# The development entrypoint uses `api:create_app --factory`, so avoid constructing
# a default app at import time and accidentally opening real DB connections in tests.
app = None
