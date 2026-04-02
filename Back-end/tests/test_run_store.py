from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from persistence.database import Base
from persistence.postgres_models import ChatMessage, ChatRun, ChatSession, Project, User
from persistence.postgres_run_store import (
    AUTO_NAME_RUN_KIND,
    ActiveChatRunExistsError,
    ActiveRunLimitExceededError,
    MESSAGE_RUN_KIND,
    PostgresRunStore,
    RunOwnershipLostError,
    RunStateConflictError,
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


class _WrappedSession:
    def __init__(self, session, on_commit=None):
        self._session = session
        self._on_commit = on_commit

    def __enter__(self):
        self._session.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._session.__exit__(exc_type, exc, tb)

    def commit(self):
        if self._on_commit is not None:
            self._on_commit(self._session)
        return self._session.commit()

    def __getattr__(self, name):
        return getattr(self._session, name)


def test_run_store_initializes_missing_run_tables_for_existing_schema():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Project.__table__,
            ChatSession.__table__,
            ChatMessage.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    run_store = PostgresRunStore(session_factory=session_factory)
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="hello",
        magi="off",
        client_request_id="same-token",
        max_active_runs_per_user=3,
    )

    assert run.id


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


def test_run_store_reuses_existing_run_after_commit_time_idempotency_conflict():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    base_session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    user, project, chat = _seed_chat(base_session_factory)
    injected = {"done": False}

    def _conflict_on_commit(_session):
        if injected["done"]:
            return
        with base_session_factory() as other_session:
            other_run = ChatRun(
                chat_session_id=chat.id,
                project_id=project.id,
                user_id=user.id,
                status="queued",
                request_content="hello",
                magi="off",
                client_request_id="same-token",
            )
            other_session.add(other_run)
            other_session.commit()
        injected["done"] = True
        raise IntegrityError("insert", {}, Exception("duplicate client request"))

    wrapped_factory = lambda: _WrappedSession(base_session_factory(), on_commit=_conflict_on_commit)
    run_store = PostgresRunStore(session_factory=wrapped_factory)

    reused = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="hello",
        magi="off",
        client_request_id="same-token",
        max_active_runs_per_user=3,
    )

    runs, total = run_store.list_runs_for_chat(chat.id, page=1, page_size=10)

    assert reused.client_request_id == "same-token"
    assert total == 1
    assert [run.id for run in runs] == [reused.id]


def test_run_store_surfaces_active_chat_conflict_after_commit_time_race():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    base_session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    user, project, chat = _seed_chat(base_session_factory)
    injected = {"done": False}

    def _conflict_on_commit(_session):
        if injected["done"]:
            return
        with base_session_factory() as other_session:
            other_run = ChatRun(
                chat_session_id=chat.id,
                project_id=project.id,
                user_id=user.id,
                status="queued",
                request_content="other",
                magi="off",
                client_request_id="other-token",
            )
            other_session.add(other_run)
            other_session.commit()
        injected["done"] = True
        raise IntegrityError("insert", {}, Exception("simulated concurrent active run"))

    wrapped_factory = lambda: _WrappedSession(base_session_factory(), on_commit=_conflict_on_commit)
    run_store = PostgresRunStore(session_factory=wrapped_factory)

    try:
        run_store.create_or_reuse_run(
            chat_session_id=chat.id,
            project_id=project.id,
            user_id=user.id,
            request_content="hello",
            magi="off",
            client_request_id="first-token",
            max_active_runs_per_user=3,
        )
        assert False, "expected active run conflict after concurrent insert"
    except ActiveChatRunExistsError:
        pass


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
    run_store.claim_next_run("worker-1", lease_seconds=30)
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
    run_store.claim_next_run("worker-1", lease_seconds=30)
    run_store.append_event(run.id, "state", "START", None)
    run_store.mark_cancelled(run.id, worker_id="worker-1", error_message="Run cancelled.")

    events = run_store.list_events_after(run.id, after_seq=0)

    assert [event.seq for event in events] == [1, 2]
    assert events[-1].type == "cancelled"


def test_cancel_queued_run_terminalizes_immediately():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="cancel me",
        magi="off",
        client_request_id="queued-cancel",
        max_active_runs_per_user=3,
    )

    cancelled = run_store.cancel_queued_run(run.id, error_message="Run cancelled.")
    terminal_event = run_store.get_terminal_event(run.id)

    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested is True
    assert cancelled.started_at is None
    assert cancelled.finished_at is not None
    assert terminal_event is not None
    assert terminal_event.type == "cancelled"
    assert terminal_event.seq == 1
    assert terminal_event.payload_json == {"message": "Run cancelled."}


def test_request_running_cancel_marks_run_for_worker_handling():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="cancel me later",
        magi="off",
        client_request_id="running-cancel",
        max_active_runs_per_user=3,
    )
    run_store.claim_next_run("worker-1", lease_seconds=30)

    pending_cancel = run_store.request_running_cancel(run.id)

    assert pending_cancel.status == "cancel_requested"
    assert pending_cancel.cancel_requested is True
    assert pending_cancel.finished_at is None
    assert run_store.get_terminal_event(run.id) is None


def test_pause_and_resume_paused_run_preserve_same_run_and_intervention_state():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="discuss this",
        magi="full",
        client_request_id="pause-me",
        max_active_runs_per_user=3,
    )
    run_store.claim_next_run("worker-1", lease_seconds=30)

    pause_requested = run_store.request_pause(run.id)
    assert pause_requested.status == "pause_requested"

    paused = run_store.mark_paused(
        run.id,
        worker_id="worker-1",
        pause_state={
            "resume_checkpoint": {"round": 1, "after_role_count": 3, "next_round": 2, "next_role_index": 0},
            "interventions": [],
        },
    )
    assert paused.status == "paused"
    assert paused.pause_state_json["resume_checkpoint"]["next_round"] == 2
    assert run_store.get_active_run_for_chat(chat.id).id == run.id

    try:
        run_store.create_or_reuse_run(
            chat_session_id=chat.id,
            project_id=project.id,
            user_id=user.id,
            request_content="second send",
            magi="off",
            client_request_id="second-send",
            max_active_runs_per_user=3,
        )
        assert False, "expected paused run to keep same-chat blocking active"
    except ActiveChatRunExistsError:
        pass

    resumed = run_store.resume_paused_run(run.id, input_text="This host is Debian 12.", input_kind="fact")
    events = run_store.list_events_after(run.id, after_seq=0)

    assert resumed.id == run.id
    assert resumed.status == "queued"
    assert resumed.pause_state_json["interventions"] == [
        {
            "entry_kind": "user_intervention",
            "role": "user",
            "phase": "discussion",
            "round": 1,
            "after_role_count": 3,
            "input_kind": "fact",
            "text": "This host is Debian 12.",
        }
    ]
    assert [event.code for event in events] == [
        "magi_pause_requested",
        "paused",
        "magi_intervention_added",
        "magi_resumed",
    ]


def test_request_running_cancel_rejects_queued_run():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="still queued",
        magi="off",
        client_request_id="queued-for-running-cancel",
        max_active_runs_per_user=3,
    )

    try:
        run_store.request_running_cancel(run.id)
        assert False, "expected queued run cancel request to be rejected"
    except RunStateConflictError:
        pass


def test_worker_owned_mutations_reject_stale_worker_after_reclaim():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="hello",
        magi="off",
        client_request_id="reclaim-stale",
        max_active_runs_per_user=3,
    )
    first_claim = run_store.claim_next_run("worker-1", lease_seconds=30)
    assert first_claim is not None

    with session_factory() as session:
        row = session.get(ChatRun, run.id)
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    reclaimed = run_store.claim_next_run("worker-2", lease_seconds=30)
    assert reclaimed is not None
    assert reclaimed.worker_id == "worker-2"

    try:
        run_store.append_event_for_worker(run.id, "worker-1", "state", "START", None)
        assert False, "expected stale worker event write to be rejected"
    except RunOwnershipLostError:
        pass

    try:
        run_store.complete_run_with_messages(
            run.id,
            worker_id="worker-1",
            user_role="user",
            user_content="hello",
            assistant_role="model",
            assistant_content="world",
        )
        assert False, "expected stale worker completion to be rejected"
    except RunOwnershipLostError:
        pass

    user_message, assistant_message = run_store.complete_run_with_messages(
        run.id,
        worker_id="worker-2",
        user_role="user",
        user_content="hello",
        assistant_role="model",
        assistant_content="world",
    )
    final_run = run_store.get_run(run.id)

    assert user_message.content == "hello"
    assert assistant_message.content == "world"
    assert final_run.status == "completed"


def test_list_runs_for_chat_paginates_orders_and_filters():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    first = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="first",
        magi="off",
        client_request_id="token-1",
        max_active_runs_per_user=3,
    )
    run_store.mark_failed(first.id, error_message="boom")

    second = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="second",
        magi="off",
        client_request_id="token-2",
        max_active_runs_per_user=3,
    )
    run_store.mark_cancelled(second.id, error_message="stop")

    third = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="third",
        magi="off",
        client_request_id="token-3",
        max_active_runs_per_user=3,
    )

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with session_factory() as session:
        runs = {
            row.id: row
            for row in session.query(ChatRun).filter(ChatRun.id.in_([first.id, second.id, third.id])).all()
        }
        runs[first.id].created_at = base_time
        runs[second.id].created_at = base_time + timedelta(minutes=1)
        runs[third.id].created_at = base_time + timedelta(minutes=2)
        session.commit()

    first_page, total = run_store.list_runs_for_chat(chat.id, page=1, page_size=2)
    second_page, second_total = run_store.list_runs_for_chat(chat.id, page=2, page_size=2)
    failed_runs, failed_total = run_store.list_runs_for_chat(chat.id, page=1, page_size=10, status="failed")

    assert total == 3
    assert second_total == 3
    assert [run.id for run in first_page] == [third.id, second.id]
    assert [run.id for run in second_page] == [first.id]

    assert failed_total == 1
    assert [run.id for run in failed_runs] == [first.id]


def test_run_store_lists_runs_for_chat_newest_first_with_pagination():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    first = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="first",
        magi="off",
        client_request_id="token-1",
        max_active_runs_per_user=3,
    )
    run_store.mark_failed(first.id, error_message="first failed")

    second = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="second",
        magi="off",
        client_request_id="token-2",
        max_active_runs_per_user=3,
    )
    run_store.mark_cancelled(second.id, error_message="second cancelled")

    third = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="third",
        magi="off",
        client_request_id="token-3",
        max_active_runs_per_user=3,
    )

    page_one, total = run_store.list_runs_for_chat(chat.id, page=1, page_size=2)
    page_two, second_total = run_store.list_runs_for_chat(chat.id, page=2, page_size=2)

    assert total == 3
    assert second_total == 3
    assert [run.id for run in page_one] == [third.id, second.id]
    assert [run.id for run in page_two] == [first.id]


def test_run_store_lists_runs_for_chat_with_status_filter():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    completed = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="done",
        magi="off",
        client_request_id="token-complete",
        max_active_runs_per_user=3,
    )
    run_store.claim_next_run("worker-1", lease_seconds=30)
    run_store.complete_run_with_messages(
        completed.id,
        worker_id="worker-1",
        user_role="user",
        user_content="done",
        assistant_role="model",
        assistant_content="done",
    )

    failed = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="fail",
        magi="off",
        client_request_id="token-fail",
        max_active_runs_per_user=3,
    )
    run_store.mark_failed(failed.id, error_message="boom")

    runs, total = run_store.list_runs_for_chat(chat.id, page=1, page_size=10, status="completed")

    assert total == 1
    assert [run.id for run in runs] == [completed.id]


def test_auto_name_runs_do_not_block_message_run_concurrency_or_active_visibility():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    auto_name_run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="Auto-name follow-up for run parent-1",
        magi="off",
        client_request_id="auto-name:parent-1",
        max_active_runs_per_user=1,
        run_kind=AUTO_NAME_RUN_KIND,
    )

    visible_active_run = run_store.get_active_run_for_chat(chat.id)
    active_by_chat = run_store.get_active_runs_for_chat_ids([chat.id])

    message_run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="real user message",
        magi="off",
        client_request_id="message-1",
        max_active_runs_per_user=1,
        run_kind=MESSAGE_RUN_KIND,
    )

    assert auto_name_run.run_kind == AUTO_NAME_RUN_KIND
    assert visible_active_run is None
    assert active_by_chat == {}
    assert message_run.run_kind == MESSAGE_RUN_KIND


def test_run_store_can_complete_auto_name_runs_without_messages():
    run_store, session_factory = _build_run_store()
    user, project, chat = _seed_chat(session_factory)

    run = run_store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="Auto-name follow-up for run parent-1",
        magi="off",
        client_request_id="auto-name:parent-1",
        max_active_runs_per_user=3,
        run_kind=AUTO_NAME_RUN_KIND,
    )
    claimed = run_store.claim_next_run("worker-auto-name", lease_seconds=30)
    assert claimed.id == run.id

    run_store.complete_run_without_messages(
        run.id,
        worker_id="worker-auto-name",
        done_payload={"debug": {"state_trace": ["AUTO_NAME", "DONE"], "tool_events": [{"type": "chat_named"}]}},
    )

    completed = run_store.get_run(run.id)
    terminal = run_store.get_terminal_event(run.id)

    assert completed.status == "completed"
    assert completed.final_user_message_id is None
    assert completed.final_assistant_message_id is None
    assert terminal is not None
    assert terminal.type == "done"
    assert terminal.payload_json["debug"]["state_trace"] == ["AUTO_NAME", "DONE"]
