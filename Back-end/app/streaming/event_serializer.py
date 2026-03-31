"""
Shared run-event serialization used by both the SSE replay path (api.py) and
the Redis fanout publish path (postgres_run_store.py).

Keeps the wire format in one place so both paths always agree.
"""


def serialize_run_event(seq, event_type, code, payload_json):
    """Return the SSE-ready dict for a run event given its raw store fields."""
    payload = payload_json or {}
    if event_type == "state":
        return {"type": "state", "seq": seq, "code": code}
    if event_type == "event":
        return {"type": "event", "seq": seq, "code": code, "payload": payload}
    if event_type == "done":
        return {"type": "done", "seq": seq, **payload}
    if event_type == "error":
        return {"type": "error", "seq": seq, "message": payload.get("message", "")}
    if event_type == "cancelled":
        return {"type": "cancelled", "seq": seq, "message": payload.get("message", "Run cancelled.")}
    return {"type": event_type, "seq": seq, "code": code, "payload": payload}
