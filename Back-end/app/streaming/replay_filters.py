def should_forward_text_delta(data, max_checkpoint_window):
    payload = data.get("payload") or {}
    window = payload.get("window")
    if window is None:
        return True
    try:
        return int(window) > int(max_checkpoint_window)
    except (TypeError, ValueError):
        return True
