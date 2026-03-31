from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.database import Base
from persistence.postgres_models import ChatSession, Project, User
from persistence.postgres_run_store import (
    ActiveChatRunExistsError,
    ActiveRunLimitExceededError,
    PostgresRunStore,
)


def _build_run_store():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PostgresRunStore(session_factory=session_factory), session_factory


def _seed_chat(session_factory, username="kayne", project_name="Debian", title="Chat"):
    with session_factory() as session:
        user = User(username=username)
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name=project_name, description="")
        session.add(project)
        session.flush()
        chat = ChatSession(project_id=project.id, title=title)
        session.add(chat)
        session.commit()
        session.refresh(user)
        session.refresh(project)
        session.refresh(chat)
        return user, project, chat


def test_run_store_reuses_same_client_request_id_per_chat():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    first = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="hello",
        magi="off",
        client_request_id="same-token",
        max_active_runs_per_user=3,
    )
    second = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="hello",
        magi="off",
        client_request_id="same-token",
        max_active_runs_per_user=3,
    )

    assert second.id == first.id


def test_run_store_blocks_second_active_run_in_same_chat():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="first",
        magi="off",
        client_request_id="first-token",
        max_active_runs_per_user=3,
    )

    try:
        run_store.create_or_reuse_run(
            chat_session_id=chat.id,
            project_id=project.id,
            user_id=user.id,
            request_content="second",
            magi="off",
            client_request_id="second-token",
            max_active_runs_per_user=3,
        )
        assert False, "expected same-chat active run conflict"
    except ActiveChatRunExistsError:
        pass


def test_run_store_enforces_configurable_per_user_cap():
    run_store, session_factory = _build_run_store()
    user, project, first_chat = _seed_chat(session_factory, title="First")
    _, _, second_chat = _seed_chat(session_factory, username="kayne-2", project_name="Other", title="Second")

    # Repoint the second chat to the same user and project so the cap is user-scoped across chats.
    with session_factory() as session:
        chat = session.get(ChatSession, second_chat.id)
        chat.project_id = project.id
        session.commit()

    run_store.create_or_reuse_run(
        chat_session_id=first_chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="first",
        magi="off",
        client_request_id="token-1",
        max_active_runs_per_user=1,
    )

    try:
        run_store.create_or_reuse_run(
            chat_session_id=second_chat.id,
            project_id=project.id,
            user_id=user.id,
            request_content="second",
            magi="off",
            client_request_id="token-2",
            max_active_runs_per_user=1,
        )
        assert False, "expected active run cap enforcement"
    except ActiveRunLimitExceededError:
        pass


def test_run_store_completion_persists_messages_and_done_event():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="hello",
        magi="off",
        client_request_id="token-1",
        max_active_runs_per_user=3,
    )
    user_message, assistant_message = run_store.complete_run_with_messages(
        run.id,
        worker_id="worker-1",
        user_role="user",
        user_content="hello",
        assistant_role="model",
        assistant_content="world",
        done_payload={"debug": {"state_trace": ["START", "DONE"]}},
    )

    completed = run_store.get_run(run.id)
    events = run_store.list_events_after(run.id, after_seq=0)

    assert completed.status == "completed"
    assert completed.final_user_message_id == user_message.id
    assert completed.final_assistant_message_id == assistant_message.id
    assert len(events) == 1
    assert events[0].type == "done"
    assert events[0].seq == 1
    assert events[0].payload_json["user_message"]["content"] == "hello"
    assert events[0].payload_json["assistant_message"]["content"] == "world"


def test_run_store_terminal_events_stay_monotonic():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="hello",
        magi="off",
        client_request_id="token-1",
        max_active_runs_per_user=3,
    )
    run_store.append_event(run.id, "state", "START", None)
    run_store.mark_cancelled(run.id, worker_id="worker-1", error_message="Run cancelled.")

    events = run_store.list_events_after(run.id, after_seq=0)

    assert [event.seq for event in events] == [1, 2]
    assert events[-1].type == "cancelled"
