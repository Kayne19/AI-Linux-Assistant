import json

try:
    import redis as _redis_lib
except ImportError:  # pragma: no cover - optional
    _redis_lib = None


WAKEUP_CHANNEL = "compute-plane:wakeup"


def get_redis_client(redis_url):
    if _redis_lib is None or not redis_url:
        return None
    try:
        client = _redis_lib.from_url(redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


def channel_name(job_id):
    return f"compute-job:{job_id}:events"


def publish_event(client, job_id, event_dict):
    if client is None:
        return
    try:
        client.publish(channel_name(job_id), json.dumps(event_dict))
    except Exception:
        pass


def subscribe_job_events(client, job_id):
    ps = client.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(channel_name(job_id))
    return ps


def publish_wakeup(client):
    if client is None:
        return
    try:
        client.publish(WAKEUP_CHANNEL, "1")
    except Exception:
        pass


def subscribe_wakeup(client):
    ps = client.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(WAKEUP_CHANNEL)
    return ps
