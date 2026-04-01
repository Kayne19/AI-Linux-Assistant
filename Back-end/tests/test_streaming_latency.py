"""Regression tests for streaming latency optimizations."""

from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.database import Base
from persistence.postgres_models import ChatSession, Project, User
from persistence.postgres_run_store import PostgresRunStore
from streaming.redis_events import WAKEUP_CHANNEL, publish_wakeup, subscribe_wakeup


def _build_run_store(redis_client=None):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PostgresRunStore(session_factory=session_factory, redis_client=redis_client), session_factory


def _seed_chat(session_factory, username="user", project_name="Project", title="Chat"):
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


def _make_run(store, session_factory, client_request_id="crid-1"):
    user, project, chat = _seed_chat(session_factory, username=f"user-{client_request_id}", title=f"chat-{client_request_id}")
    return store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="hello",
        magi="off",
        client_request_id=client_request_id,
        max_active_runs_per_user=5,
    )


def test_publish_wakeup_sends_signal():
    client = MagicMock()

    publish_wakeup(client)

    client.publish.assert_called_once_with(WAKEUP_CHANNEL, "1")


def test_subscribe_wakeup_uses_wakeup_channel():
    client = MagicMock()
    pubsub = MagicMock()
    client.pubsub.return_value = pubsub

    result = subscribe_wakeup(client)

    client.pubsub.assert_called_once_with(ignore_subscribe_messages=True)
    pubsub.subscribe.assert_called_once_with(WAKEUP_CHANNEL)
    assert result is pubsub


def test_create_run_publishes_wakeup_signal():
    redis_client = MagicMock()
    store, session_factory = _build_run_store(redis_client=redis_client)

    _make_run(store, session_factory)

    redis_client.publish.assert_called_once_with(WAKEUP_CHANNEL, "1")


def test_append_text_checkpoint_writes_event_and_accumulates_text():
    store, session_factory = _build_run_store()
    run = _make_run(store, session_factory)

    store.append_text_checkpoint(run.id, "Hello", 0)
    store.append_text_checkpoint(run.id, " world", 1)

    refreshed = store.get_run(run.id)
    events = store.list_events_after(run.id, after_seq=0)

    assert refreshed.partial_assistant_text == "Hello world"
    assert [event.code for event in events] == ["text_checkpoint", "text_checkpoint"]
    assert events[0].payload_json == {"text": "Hello", "window": 0}
    assert events[1].payload_json == {"text": "Hello world", "window": 1}


def test_append_text_checkpoint_publishes_checkpoint_event():
    redis_client = MagicMock()
    store, session_factory = _build_run_store(redis_client=redis_client)
    run = _make_run(store, session_factory)
    redis_client.publish.reset_mock()

    store.append_text_checkpoint(run.id, "Hello", 0)

    redis_client.publish.assert_called_once()
    channel, raw_payload = redis_client.publish.call_args[0]
    assert channel == f"run:{run.id}:events"
    assert '"code": "text_checkpoint"' in raw_payload


def test_should_forward_text_delta_without_checkpoint():
    from streaming.replay_filters import should_forward_text_delta

    assert should_forward_text_delta({"payload": {"window": 0}}, max_checkpoint_window=-1) is True
    assert should_forward_text_delta({"payload": {"window": 2}}, max_checkpoint_window=-1) is True


def test_should_forward_text_delta_drops_covered_windows():
    from streaming.replay_filters import should_forward_text_delta

    assert should_forward_text_delta({"payload": {"window": 0}}, max_checkpoint_window=0) is False
    assert should_forward_text_delta({"payload": {"window": 1}}, max_checkpoint_window=0) is True


def test_should_forward_text_delta_allows_legacy_payloads():
    from streaming.replay_filters import should_forward_text_delta

    assert should_forward_text_delta({"payload": {}}, max_checkpoint_window=5) is True
    assert should_forward_text_delta({}, max_checkpoint_window=5) is True


def test_delta_buffer_publishes_immediately_and_flushes_in_batches():
    from chat_run_worker import _DeltaBuffer

    store, session_factory = _build_run_store()
    run = _make_run(store, session_factory)
    published = []

    buffer = _DeltaBuffer(
        run_id=run.id,
        run_store=store,
        redis_publish_fn=lambda run_id, delta, window: published.append((run_id, delta, window)),
        flush_interval=999,
        flush_bytes=999,
    )

    buffer.push("Hello")
    buffer.push(" world")

    assert published == [
        (run.id, "Hello", 0),
        (run.id, " world", 0),
    ]
    assert store.list_events_after(run.id, after_seq=0) == []

    buffer.flush()
    events = store.list_events_after(run.id, after_seq=0)
    assert len(events) == 1
    assert events[0].payload_json == {"text": "Hello world", "window": 0}


def test_delta_buffer_advances_window_after_each_flush():
    from chat_run_worker import _DeltaBuffer

    store, session_factory = _build_run_store()
    run = _make_run(store, session_factory)

    buffer = _DeltaBuffer(
        run_id=run.id,
        run_store=store,
        redis_publish_fn=lambda *_args: None,
        flush_interval=999,
        flush_bytes=999,
    )

    buffer.push("A")
    buffer.flush()
    buffer.push("B")
    buffer.flush()

    events = store.list_events_after(run.id, after_seq=0)
    assert [event.payload_json["window"] for event in events] == [0, 1]


def test_delta_buffer_empty_flush_is_noop():
    from chat_run_worker import _DeltaBuffer

    store, session_factory = _build_run_store()
    run = _make_run(store, session_factory)

    buffer = _DeltaBuffer(
        run_id=run.id,
        run_store=store,
        redis_publish_fn=lambda *_args: None,
        flush_interval=999,
        flush_bytes=999,
    )

    buffer.flush()
    buffer.flush()

    assert store.list_events_after(run.id, after_seq=0) == []
