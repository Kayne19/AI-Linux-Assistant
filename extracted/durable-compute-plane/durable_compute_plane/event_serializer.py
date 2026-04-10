def serialize_job_event(seq, event_type, code, payload_json, created_at=None):
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
            "message": payload.get("message", "Job cancelled."),
            "created_at": created_at_value,
        }
    if event_type == "paused":
        return {
            "type": "paused",
            "seq": seq,
            "message": payload.get("message", "Job paused."),
            "payload": payload,
            "created_at": created_at_value,
        }
    return {"type": event_type, "seq": seq, "code": code, "payload": payload, "created_at": created_at_value}
