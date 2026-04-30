from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.database import Base
from persistence.postgres_models import ChatSession, Project, User
from persistence.postgres_run_store import PostgresRunStore
from chat_run_worker import ChatRunWorkerService


def _build_run_store():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PostgresRunStore(session_factory=session_factory), session_factory


def _seed_chat(session_factory):
    with session_factory() as session:
        user = User(username="worker-test")
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Project", description="")
        session.add(project)
        session.flush()
        chat = ChatSession(project_id=project.id, title="Chat")
        session.add(chat)
        session.commit()
        session.refresh(user)
        session.refresh(project)
        session.refresh(chat)
        return user, project, chat


class _FakeRouter:
    def set_state_listener(self, listener):
        self._state_listener = listener

    def set_event_listener(self, listener):
        self._event_listener = listener

    def run_magi_resumption(self, pause_state, stream_response=True, magi="full"):
        del pause_state, stream_response, magi
        return SimpleNamespace(
            response="resumed answer",
            council_entries=[],
            state_trace=["START", "GENERATE_RESPONSE", "DONE"],
            tool_events=[],
            retrieved_docs="",
            retrieval_query="",
            schedule_auto_name=False,
        )


class _TestWorkerService(ChatRunWorkerService):
    def __init__(self, run_store):
        self.settings = SimpleNamespace(chat_run_lease_seconds=30)
        self.worker_id = "worker-test"
        self.run_store = run_store
        self.app_store = None
        self._stop_event = None
        self._shared_retrieval_components = None

    def _build_router(self, run):
        del run
        return _FakeRouter()

    def _queue_auto_name_run(self, run, turn, claimed_worker_id):
        del run, turn, claimed_worker_id
        return None


def test_worker_allows_resumed_paused_magi_run_with_existing_event_history_to_complete():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="original request",
        magi="full",
        client_request_id="resume-worker-test",
        max_active_runs_per_user=3,
    )
    run_store.claim_next_run("worker-1", lease_seconds=30)
    run_store.mark_paused(
        run.id,
        worker_id="worker-1",
        pause_state={"resume_checkpoint": {"round": 1, "after_role_count": 3}},
        event_payload={"message": "Run paused."},
    )
    resumed = run_store.resume_paused_run(
        run.id, input_text="extra fact", input_kind="fact"
    )
    claimed = run_store.claim_next_run("worker-2", lease_seconds=30)

    assert resumed.id == run.id
    assert claimed is not None
    assert claimed.id == run.id
    assert claimed.status == "running"
    assert int(claimed.latest_event_seq or 0) > 0
    assert claimed.pause_state_json is not None

    service = _TestWorkerService(run_store)
    service._handle_claimed_run(claimed, "worker-2")

    finished = run_store._get_run(run.id)
    assert finished.status == "completed", (
        f"Run failed with error: {finished.error_message}"
    )
    assert finished.error_message == ""

    runs, total = run_store.list_runs_for_chat(chat.id, page=1, page_size=10)
    assert total == 1
    assert runs[0].status == "completed"
