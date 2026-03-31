import os


def debug_enabled():
    return os.getenv("AI_LINUX_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def debug_print(*args, **kwargs):
    if debug_enabled():
        print(*args, **kwargs)
