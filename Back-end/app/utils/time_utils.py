from datetime import datetime, timezone


def _utc_now():
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _iso(value):
    """Return ISO format string, or '' if value is None."""
    return value.isoformat() if value is not None else ""
