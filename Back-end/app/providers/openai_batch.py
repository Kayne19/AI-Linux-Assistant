"""Transport-only wrapper around the OpenAI Batch API.

This module provides a thin client that handles the four Batch API operations:
  1. Upload a JSONL file of requests (files.create)
  2. Submit a batch job (batches.create)
  3. Poll batch status (batches.retrieve)
  4. Download output or error file content (files.content)

No response-shape logic, prompt-cache key construction, or merge semantics live
here — those belong to higher-level stages (T9/T10).

All retryable operations (upload, submit, status poll, content download) use
exponential backoff on 429 rate-limit errors, mirroring the pattern in
OpenAICaller._create_response_with_retries.
"""

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy OpenAI SDK import (same pattern as openAI_caller.py)
# ---------------------------------------------------------------------------
try:
    from openai import OpenAI as _OpenAI
except ImportError:  # pragma: no cover
    _OpenAI = None

# ---------------------------------------------------------------------------
# Terminal status set
# ---------------------------------------------------------------------------
TERMINAL_STATUSES: frozenset = frozenset({"completed", "failed", "expired", "cancelled"})


def is_terminal(status: str) -> bool:
    """Return True if *status* is a terminal Batch API state."""
    return status in TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BatchSubmission:
    batch_id: str
    input_file_id: str
    status: str  # raw status from OpenAI
    created_at: int | None = None


@dataclass(frozen=True)
class BatchStatus:
    batch_id: str
    status: str  # e.g. "validating", "in_progress", "finalizing", "completed", "failed", "expired", "cancelled"
    output_file_id: str | None
    error_file_id: str | None
    request_counts: dict  # {"total": int, "completed": int, "failed": int}
    completed_at: int | None


# ---------------------------------------------------------------------------
# Internal retry helpers
# ---------------------------------------------------------------------------

def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True if *exc* looks like a 429 / rate-limit error.

    Mirrors OpenAICaller._is_rate_limit_error exactly.
    """
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
    """Compute backoff delay from error message or exponential formula.

    Mirrors OpenAICaller._extract_retry_delay_seconds exactly.
    """
    message = str(exc)
    match = re.search(r"try again in\s+(\d+(?:\.\d+)?)ms", message, re.IGNORECASE)
    if match:
        return max(float(match.group(1)) / 1000.0, 1.0)

    match = re.search(r"try again in\s+(\d+(?:\.\d+)?)s", message, re.IGNORECASE)
    if match:
        return max(float(match.group(1)), 1.0)

    return min(1.0 * (2 ** max(0, attempt_number - 1)), 80.0)


def _with_retries(fn, max_retries: int = 12):
    """Call *fn()* and retry on rate-limit errors with exponential backoff.

    Args:
        fn: A zero-argument callable that performs the API call.
        max_retries: Maximum number of retry attempts (not counting the first).

    Returns:
        The return value of *fn()* on success.

    Raises:
        The last exception if it is not a rate-limit error, or if *max_retries*
        attempts have been exhausted.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            attempt += 1
            if not _is_rate_limit_error(exc) or attempt > max_retries:
                raise
            delay = _extract_retry_delay_seconds(exc, attempt)
            logger.warning(
                "OpenAI Batch API rate-limited; retrying in %.1fs (attempt %d/%d)",
                delay,
                attempt,
                max_retries,
            )
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------

class OpenAIBatchClient:
    """Thin transport wrapper around the four OpenAI Batch API operations.

    Args:
        client: An already-constructed ``openai.OpenAI`` instance.  If ``None``
            (the default) a new client is constructed from environment variables
            via ``OpenAI()``.

    Raises:
        RuntimeError: On first use if the ``openai`` package is not installed.
    """

    def __init__(self, client=None):
        if client is None:
            if _OpenAI is None:  # pragma: no cover
                raise RuntimeError(
                    "OpenAI SDK is not installed. Install the 'openai' package to use this provider."
                )
            client = _OpenAI()
        self._client = client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload_jsonl(self, jsonl_path: Path) -> str:
        """Upload *jsonl_path* for use as a Batch API input file.

        Args:
            jsonl_path: Path to the ``.jsonl`` file on disk.

        Returns:
            The ``file_id`` string returned by the Files API.
        """
        jsonl_path = Path(jsonl_path)

        def _do_upload():
            with open(jsonl_path, "rb") as fh:
                response = self._client.files.create(file=fh, purpose="batch")
            logger.debug("Uploaded batch input file: %s -> %s", jsonl_path, response.id)
            return response.id

        return _with_retries(_do_upload)

    def submit_batch(
        self,
        input_file_id: str,
        *,
        endpoint: str = "/v1/responses",
        completion_window: str = "24h",
        metadata: dict | None = None,
    ) -> BatchSubmission:
        """Create a batch job pointing at *input_file_id*.

        Args:
            input_file_id: The file ID returned by :meth:`upload_jsonl`.
            endpoint: The target API endpoint.  Defaults to ``"/v1/responses"``.
            completion_window: How long OpenAI should attempt to run the batch.
            metadata: Optional key/value metadata attached to the batch object.

        Returns:
            A :class:`BatchSubmission` with the batch ID and initial status.
        """
        def _do_submit():
            resp = self._client.batches.create(
                input_file_id=input_file_id,
                endpoint=endpoint,
                completion_window=completion_window,
                metadata=metadata or {},
            )
            logger.debug(
                "Submitted batch %s (input_file=%s, status=%s)",
                resp.id,
                input_file_id,
                resp.status,
            )
            return BatchSubmission(
                batch_id=resp.id,
                input_file_id=input_file_id,
                status=resp.status,
                created_at=getattr(resp, "created_at", None),
            )

        return _with_retries(_do_submit)

    def get_status(self, batch_id: str) -> BatchStatus:
        """Retrieve current metadata for batch *batch_id*.

        Args:
            batch_id: The batch ID returned by :meth:`submit_batch`.

        Returns:
            A :class:`BatchStatus` snapshot.
        """
        def _do_retrieve():
            resp = self._client.batches.retrieve(batch_id)

            # request_counts may be an object with attributes OR a dict,
            # depending on SDK version.
            rc = getattr(resp, "request_counts", None)
            if rc is None:
                counts = {"total": 0, "completed": 0, "failed": 0}
            elif isinstance(rc, dict):
                counts = {
                    "total": rc.get("total", 0),
                    "completed": rc.get("completed", 0),
                    "failed": rc.get("failed", 0),
                }
            else:
                counts = {
                    "total": getattr(rc, "total", 0),
                    "completed": getattr(rc, "completed", 0),
                    "failed": getattr(rc, "failed", 0),
                }

            return BatchStatus(
                batch_id=batch_id,
                status=resp.status,
                output_file_id=getattr(resp, "output_file_id", None),
                error_file_id=getattr(resp, "error_file_id", None),
                request_counts=counts,
                completed_at=getattr(resp, "completed_at", None),
            )

        return _with_retries(_do_retrieve)

    def download_output(self, file_id: str, dest_path: Path) -> Path:
        """Download the content of a batch output (or error) file to *dest_path*.

        The whole fetch+drain is wrapped in retry logic so a mid-stream
        network blip doesn't drop the file silently — re-running the fetch
        is cheap relative to losing the result.

        Args:
            file_id: The output or error file ID from :class:`BatchStatus`.
            dest_path: Destination path on disk.  Parent directory must exist.

        Returns:
            *dest_path* for caller convenience.
        """
        dest_path = Path(dest_path)

        def _fetch_and_drain() -> bytes:
            content_response = self._client.files.content(file_id)
            # The SDK may return an object with .read() (binary) or
            # .iter_bytes(). Try .read() first, fall back to .iter_bytes().
            if hasattr(content_response, "read"):
                return content_response.read()
            chunks = []
            for chunk in content_response.iter_bytes():
                chunks.append(chunk)
            return b"".join(chunks)

        raw = _with_retries(_fetch_and_drain)

        dest_path.write_bytes(raw)
        logger.debug("Downloaded file %s -> %s (%d bytes)", file_id, dest_path, len(raw))
        return dest_path
