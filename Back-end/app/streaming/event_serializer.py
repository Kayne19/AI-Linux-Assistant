"""
Shared run-event serialization used by both the SSE replay path (api.py) and
the Redis fanout publish path (postgres_run_store.py).

Keeps the wire format in one place so both paths always agree.
"""


def serialize_run_event(seq, event_type, code, payload_json, created_at=None):
    """Return the SSE-ready dict for a run event given its raw store fields."""
    payload = payload_json or {}
    created_at_value = created_at.isoformat() if hasattr(created_at, "isoformat") else (created_at or "")
    if event_type == "state":
        return {"type": "state", "seq": seq, "code": code, "created_at": created_at_value}
    if event_type == "event":
        return {"type": "event", "seq": seq, "code": code, "payload": payload, "created_at": created_at_value}
    if event_type == "done":
        return {"type": "done", "seq": seq, "created_at": created_at_value, **payload}
    if event_type == "error":
        return {"type": "error", "seq": seq, "message": payload.get("message", ""), "created_at": created_at_value}
    if event_type == "cancelled":
        return {
            "type": "cancelled",
            "seq": seq,
            "message": payload.get("message", "Run cancelled."),
            "created_at": created_at_value,
        }
    if event_type == "paused":
        return {
            "type": "paused",
            "seq": seq,
            "message": payload.get("message", "Run paused."),
            "created_at": created_at_value,
            "payload": payload,
        }
    return {"type": event_type, "seq": seq, "code": code, "payload": payload, "created_at": created_at_value}
