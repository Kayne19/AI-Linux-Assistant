import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from api import _terminal_event_payload
from persistence.database import Base
from persistence.postgres_models import ChatSession, Project, User
from persistence.postgres_run_store import PostgresRunStore


class _StubAppStore:
    def get_message(self, _message_id):
        return None


def _build_run_store():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PostgresRunStore(session_factory=session_factory), session_factory


def _seed_chat(session_factory):
    with session_factory() as session:
        user = User(username="api-test")
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


def test_terminal_event_payload_prefers_durable_done_event():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="hello",
        magi="off",
        client_request_id="terminal-payload",
        max_active_runs_per_user=3,
    )
    run_store.claim_next_run("worker-1", lease_seconds=30)
    run_store.complete_run_with_messages(
        run.id,
        worker_id="worker-1",
        user_role="user",
        user_content="hello",
        assistant_role="model",
        assistant_content="world",
        done_payload={
            "debug": {
                "state_trace": ["START", "DONE"],
                "tool_events": [{"type": "x"}],
                "normalized_inputs": {
                    "request_text": "hello",
                    "conversation_summary_text": "Older summary",
                    "recent_turns": [{"role": "user", "content": "Earlier"}],
                    "memory_snapshot_text": "KNOWN SYSTEM PROFILE:\n- OS: Debian",
                    "retrieval_query": "install package",
                    "retrieved_context_text": "---\n[Source: guide.pdf (Page 4)]\napt install foo\n",
                    "retrieved_context_blocks": [
                        {
                            "source": "guide.pdf",
                            "pages": [4],
                            "page_label": "Page 4",
                            "text": "apt install foo",
                        }
                    ],
                },
            }
        },
    )

    payload = _terminal_event_payload(run_store, run.id, run_store.get_run(run.id), _StubAppStore())

    assert payload is not None
    assert payload["type"] == "done"
    assert payload["user_message"]["content"] == "hello"
    assert payload["assistant_message"]["content"] == "world"
    assert payload["debug"]["state_trace"] == ["START", "DONE"]
    assert payload["debug"]["tool_events"] == [{"type": "x"}]
    assert payload["debug"]["normalized_inputs"]["retrieved_context_blocks"][0]["source"] == "guide.pdf"


def test_terminal_event_payload_prefers_durable_cancelled_event():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="stop",
        magi="off",
        client_request_id="cancelled-terminal-payload",
        max_active_runs_per_user=3,
    )
    run_store.cancel_queued_run(run.id, error_message="Run cancelled.")

    payload = _terminal_event_payload(run_store, run.id, run_store.get_run(run.id), _StubAppStore())

    assert payload is not None
    assert payload["type"] == "cancelled"
    assert payload["seq"] == 1
    assert payload["message"] == "Run cancelled."
    assert payload["created_at"]
