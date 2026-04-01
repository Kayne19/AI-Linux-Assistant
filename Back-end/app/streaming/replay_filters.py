ASSISTANT_STREAM_KEY = "__assistant__"


def should_forward_text_delta(data, max_checkpoint_window):
    payload = data.get("payload") or {}
    window = payload.get("window")
    if window is None:
        return True
    try:
        return int(window) > int(max_checkpoint_window)
    except (TypeError, ValueError):
        return True


def stream_window_key(data):
    code = data.get("code")
    payload = data.get("payload") or {}
    if code in {"text_delta", "text_checkpoint"}:
        return ASSISTANT_STREAM_KEY
    if code in {"magi_role_text_delta", "magi_role_text_checkpoint"}:
        role = str(payload.get("role") or "")
        phase = str(payload.get("phase") or "")
        round_number = payload.get("round")
        try:
            normalized_round = 0 if round_number is None else int(round_number)
        except (TypeError, ValueError):
            normalized_round = 0
        return f"{phase}:{role}:{normalized_round}"
    return None


def register_checkpoint_window(checkpoint_windows, data):
    key = stream_window_key(data)
    if key is None:
        return
    payload = data.get("payload") or {}
    window = payload.get("window")
    try:
        checkpoint_windows[key] = max(int(checkpoint_windows.get(key, -1)), int(window))
    except (TypeError, ValueError):
        return


def should_forward_stream_delta(data, checkpoint_windows):
    key = stream_window_key(data)
    if key is None:
        return True
    return should_forward_text_delta(data, checkpoint_windows.get(key, -1))
