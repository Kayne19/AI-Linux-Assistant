"""
Tests for the Redis fanout layer and the shared event serializer.

Covers:
- serialize_run_event() wire format for every event type
- PostgresRunStore._publish() is called after each of the four event-write methods
- Redis publish failures never propagate to the caller
- get_redis_client() returns None when url is absent or redis-py is missing
"""
import json
from unittest.mock import MagicMock, call, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.database import Base
from persistence.postgres_models import ChatSession, Project, User
from persistence.postgres_run_store import PostgresRunStore
from streaming.event_serializer import serialize_run_event
from streaming.redis_events import get_redis_client, publish_event, channel_name


# ---------------------------------------------------------------------------
# Helpers shared with test_run_store.py
# ---------------------------------------------------------------------------

def _build_run_store(redis_client=None):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PostgresRunStore(session_factory=sf, redis_client=redis_client), sf


def _seed_chat(session_factory, username="u", project_name="P", title="T"):
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


def _make_run(store, sf):
    user, project, chat = _seed_chat(sf)
    run = store.create_or_reuse_run(
        chat_session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        request_content="hello",
        magi="off",
        client_request_id="crid-1",
        max_active_runs_per_user=5,
    )
    publish = getattr(getattr(store, "_redis_client", None), "publish", None)
    reset_mock = getattr(publish, "reset_mock", None)
    if callable(reset_mock):
        reset_mock()
    return run


# ---------------------------------------------------------------------------
# serialize_run_event
# ---------------------------------------------------------------------------

def test_serialize_state():
    result = serialize_run_event(1, "state", "CLASSIFY", None)
    assert result == {"type": "state", "seq": 1, "code": "CLASSIFY"}


def test_serialize_event():
    result = serialize_run_event(2, "event", "text_delta", {"delta": "hi"})
    assert result == {"type": "event", "seq": 2, "code": "text_delta", "payload": {"delta": "hi"}}


def test_serialize_done_spreads_payload():
    payload = {"user_message": {"id": 1}, "assistant_message": {"id": 2}, "debug": {}}
    result = serialize_run_event(3, "done", "done", payload)
    assert result["type"] == "done"
    assert result["seq"] == 3
    assert result["user_message"] == {"id": 1}
    assert result["assistant_message"] == {"id": 2}


def test_serialize_error():
    result = serialize_run_event(4, "error", "error", {"message": "boom"})
    assert result == {"type": "error", "seq": 4, "message": "boom"}


def test_serialize_cancelled():
    result = serialize_run_event(5, "cancelled", "cancelled", {"message": "gone"})
    assert result == {"type": "cancelled", "seq": 5, "message": "gone"}


def test_serialize_unknown_type():
    result = serialize_run_event(6, "custom", "x", {"k": "v"})
    assert result == {"type": "custom", "seq": 6, "code": "x", "payload": {"k": "v"}}


# ---------------------------------------------------------------------------
# publish_event helper
# ---------------------------------------------------------------------------

def test_publish_event_sends_json_to_correct_channel():
    client = MagicMock()
    publish_event(client, "run-abc", {"type": "state", "seq": 1, "code": "START"})
    client.publish.assert_called_once_with(
        "run:run-abc:events",
        json.dumps({"type": "state", "seq": 1, "code": "START"}),
    )


def test_publish_event_noop_when_client_none():
    publish_event(None, "run-abc", {"type": "state", "seq": 1, "code": "START"})
    # No exception raised


def test_publish_event_swallows_redis_error():
    client = MagicMock()
    client.publish.side_effect = RuntimeError("connection lost")
    publish_event(client, "run-abc", {"type": "state"})
    # No exception raised


def test_channel_name():
    assert channel_name("abc-123") == "run:abc-123:events"


# ---------------------------------------------------------------------------
# get_redis_client
# ---------------------------------------------------------------------------

def test_get_redis_client_returns_none_when_url_empty():
    assert get_redis_client(None) is None
    assert get_redis_client("") is None


def test_get_redis_client_returns_none_when_redis_unavailable():
    # Simulate redis-py failing to connect
    with patch("streaming.redis_events._redis_lib") as mock_lib:
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("refused")
        mock_lib.from_url.return_value = mock_client
        result = get_redis_client("redis://localhost:6379/0")
    assert result is None


# ---------------------------------------------------------------------------
# PostgresRunStore._publish integration
# ---------------------------------------------------------------------------

def test_append_event_publishes_to_redis():
    mock_redis = MagicMock()
    store, sf = _build_run_store(redis_client=mock_redis)
    run = _make_run(store, sf)

    store.append_event(run.id, "state", "START", None)

    mock_redis.publish.assert_called_once()
    channel, data = mock_redis.publish.call_args[0]
    assert channel == f"run:{run.id}:events"
    payload = json.loads(data)
    assert payload["type"] == "state"
    assert payload["code"] == "START"
    assert payload["seq"] == 1


def test_mark_failed_publishes_error_event():
    mock_redis = MagicMock()
    store, sf = _build_run_store(redis_client=mock_redis)
    run = _make_run(store, sf)
    store.claim_next_run("w1", lease_seconds=30)

    store.mark_failed(run.id, worker_id="w1", error_message="oops")

    mock_redis.publish.assert_called_once()
    payload = json.loads(mock_redis.publish.call_args[0][1])
    assert payload["type"] == "error"
    assert payload["message"] == "oops"


def test_mark_cancelled_publishes_cancelled_event():
    mock_redis = MagicMock()
    store, sf = _build_run_store(redis_client=mock_redis)
    run = _make_run(store, sf)
    store.claim_next_run("w1", lease_seconds=30)

    store.mark_cancelled(run.id, worker_id="w1", error_message="bye")

    mock_redis.publish.assert_called_once()
    payload = json.loads(mock_redis.publish.call_args[0][1])
    assert payload["type"] == "cancelled"
    assert payload["message"] == "bye"


def test_complete_run_publishes_done_event():
    mock_redis = MagicMock()
    store, sf = _build_run_store(redis_client=mock_redis)
    run = _make_run(store, sf)
    store.claim_next_run("w1", lease_seconds=30)

    store.complete_run_with_messages(
        run.id,
        worker_id="w1",
        user_role="user",
        user_content="hello",
        assistant_role="model",
        assistant_content="hi there",
    )

    mock_redis.publish.assert_called_once()
    payload = json.loads(mock_redis.publish.call_args[0][1])
    assert payload["type"] == "done"
    assert "user_message" in payload
    assert "assistant_message" in payload


def test_publish_failure_does_not_crash_append_event():
    mock_redis = MagicMock()
    mock_redis.publish.side_effect = RuntimeError("Redis down")
    store, sf = _build_run_store(redis_client=mock_redis)
    run = _make_run(store, sf)

    # Must not raise despite Redis failure
    store.append_event(run.id, "state", "START", None)


def test_no_redis_client_skips_publish():
    store, sf = _build_run_store(redis_client=None)
    run = _make_run(store, sf)
    # Should work silently with no redis client
    store.append_event(run.id, "state", "START", None)
    store.mark_failed(run.id, error_message="x")
