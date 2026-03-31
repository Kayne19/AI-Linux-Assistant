import json
import queue
import threading
from typing import Any

from orchestration.model_router import ModelRouter
from persistence.postgres_app_store import PostgresAppStore
from persistence.postgres_memory_store import PostgresMemoryStore

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


class MessageCreateRequest(BaseModel):
    content: str = Field(..., min_length=1)
    magi: str = "off"


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


class ChatSessionResponse(BaseModel):
    id: str
    project_id: str
    title: str
    created_at: str
    updated_at: str


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


def _serialize_chat_session(chat_session):
    return ChatSessionResponse(
        id=chat_session.id,
        project_id=chat_session.project_id,
        title=(chat_session.title or "").strip(),
        created_at=_iso(getattr(chat_session, "created_at", None)),
        updated_at=_iso(getattr(chat_session, "updated_at", None)),
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


def _build_router_for_session(app_store, chat_session_id):
    context = app_store.get_session_context(chat_session_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Chat session not found.")

    memory_store = PostgresMemoryStore(project_id=context["project_id"])
    return ModelRouter(
        memory_store=memory_store,
        chat_store=app_store,
        chat_session_id=chat_session_id,
    )


def create_app():
    _require_fastapi()
    app = FastAPI(title="AI Linux Assistant API")
    app_store = PostgresAppStore()

    if CORSMiddleware is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/auth/login", response_model=UserResponse)
    def login(request: LoginRequest):
        user = app_store.find_or_create_user(request.username)
        return _serialize_user(user)

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
        project = app_store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return [_serialize_chat_session(chat) for chat in app_store.list_chat_sessions(project_id)]

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
        return _serialize_chat_session(chat_session)

    @app.patch("/chats/{chat_session_id}", response_model=ChatSessionResponse)
    def update_chat(chat_session_id: str, request: ChatUpdateRequest):
        chat_session = app_store.get_chat_session(chat_session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        try:
            updated_chat = app_store.update_chat_session_title(chat_session_id, request.title)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _serialize_chat_session(updated_chat)

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
        chat_session = app_store.get_chat_session(chat_session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        return [_serialize_message(message) for message in app_store.list_messages(chat_session_id)]

    @app.post("/chats/{chat_session_id}/messages", response_model=SendMessageResponse)
    def send_message(chat_session_id: str, request: MessageCreateRequest):
        chat_session = app_store.get_chat_session(chat_session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")

        router = _build_router_for_session(app_store, chat_session_id)
        previous_count = len(router.get_history())
        response_text = router.ask_question(request.content, magi=request.magi)
        persisted_messages = app_store.list_messages(chat_session_id)

        if len(persisted_messages) < previous_count + 2:
            raise HTTPException(status_code=500, detail="Router did not persist the expected chat messages.")

        user_message = _serialize_message(persisted_messages[-2])
        assistant_message = _serialize_message(persisted_messages[-1])
        turn = router.last_turn
        debug = AssistantDebugResponse(
            state_trace=list(getattr(turn, "state_trace", []) or []),
            tool_events=list(getattr(turn, "tool_events", []) or []),
            retrieval_query=getattr(turn, "retrieval_query", "") or "",
            retrieved_sources=_extract_sources(getattr(turn, "retrieved_docs", "") or ""),
        )
        return SendMessageResponse(
            user_message=user_message,
            assistant_message=assistant_message,
            debug=debug,
        )

    @app.post("/chats/{chat_session_id}/messages/stream")
    def send_message_stream(chat_session_id: str, request: MessageCreateRequest):
        chat_session = app_store.get_chat_session(chat_session_id)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")

        router = _build_router_for_session(app_store, chat_session_id)
        previous_count = len(router.get_history())
        event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def on_state(state, _turn):
            event_queue.put({"type": "state", "code": state.name})

        def on_event(event_type, payload):
            event_queue.put({"type": "event", "code": event_type, "payload": payload})

        def run_router():
            try:
                router.set_state_listener(on_state)
                router.set_event_listener(on_event)
                response_text = router.ask_question_stream(request.content, magi=request.magi)
                persisted_messages = app_store.list_messages(chat_session_id)

                if response_text.startswith("Router error:"):
                    event_queue.put({"type": "error", "message": response_text})
                    return

                if len(persisted_messages) < previous_count + 2:
                    event_queue.put(
                        {
                            "type": "error",
                            "message": "Router did not persist the expected chat messages.",
                        }
                    )
                    return

                user_message = _serialize_message(persisted_messages[-2])
                assistant_message = _serialize_message(persisted_messages[-1])
                turn = router.last_turn
                debug = AssistantDebugResponse(
                    state_trace=list(getattr(turn, "state_trace", []) or []),
                    tool_events=list(getattr(turn, "tool_events", []) or []),
                    retrieval_query=getattr(turn, "retrieval_query", "") or "",
                    retrieved_sources=_extract_sources(getattr(turn, "retrieved_docs", "") or ""),
                )
                event_queue.put(
                    {
                        "type": "done",
                        "user_message": _model_dump(user_message),
                        "assistant_message": _model_dump(assistant_message),
                        "debug": _model_dump(debug),
                    }
                )
            except Exception as exc:
                event_queue.put({"type": "error", "message": str(exc)})
            finally:
                event_queue.put(None)

        threading.Thread(target=run_router, daemon=True).start()

        def stream():
            while True:
                item = event_queue.get()
                if item is None:
                    break
                yield _sse_payload(item)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app
app = create_app() if FastAPI is not None else None
