"""
Redis pub/sub fanout for active run streaming.

Sole module that touches redis-py. All other modules use get_shared_client(),
publish_event(), and subscribe_run_events().

When REDIS_URL is absent or Redis is unreachable the module degrades silently:
get_shared_client() returns None, and callers fall back to Postgres polling.
"""
import json
import threading

try:
    import redis as _redis_lib
except ImportError:  # pragma: no cover - redis is optional
    _redis_lib = None

_lock = threading.Lock()
_shared_client = None
_initialized = False


def get_redis_client(redis_url):
    """Return a connected Redis client (decode_responses=True) or None."""
    if _redis_lib is None or not redis_url:
        return None
    try:
        client = _redis_lib.from_url(redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


def get_shared_client():
    """Lazy singleton: return the app-wide Redis client, or None if not configured."""
    global _shared_client, _initialized
    if _initialized:
        return _shared_client
    with _lock:
        if not _initialized:
            from config.settings import SETTINGS
            _shared_client = get_redis_client(getattr(SETTINGS, "redis_url", None))
            _initialized = True
    return _shared_client


def channel_name(run_id):
    return f"run:{run_id}:events"


def publish_event(client, run_id, event_dict):
    """PUBLISH event_dict as JSON to the run's fanout channel. Never raises."""
    if client is None:
        return
    try:
        client.publish(channel_name(run_id), json.dumps(event_dict))
    except Exception:
        pass


def subscribe_run_events(client, run_id):
    """Return a PubSub object already subscribed to the run's channel."""
    ps = client.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(channel_name(run_id))
    return ps
