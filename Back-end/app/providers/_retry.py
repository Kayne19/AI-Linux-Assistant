"""Shared rate-limit error detection and retry-delay extraction.

Used by both OpenAI caller (streaming + response API) and the batch client.
"""

import re


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True if *exc* looks like a 429 / rate-limit error."""
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True

    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True

    code = getattr(exc, "code", None)
    if code == "rate_limit_exceeded":
        return True

    message = str(exc).lower()
    return "rate limit" in message or "429" in message


def _extract_retry_delay_seconds(exc: Exception, attempt_number: int) -> float:
    """Compute backoff delay from error message or exponential formula."""
    message = str(exc)
    match = re.search(r"try again in\s+(\d+(?:\.\d+)?)ms", message, re.IGNORECASE)
    if match:
        return max(float(match.group(1)) / 1000.0, 1.0)

    match = re.search(r"try again in\s+(\d+(?:\.\d+)?)s", message, re.IGNORECASE)
    if match:
        return max(float(match.group(1)), 1.0)

    return min(1.0 * (2 ** max(0, attempt_number - 1)), 80.0)
