"""FsmProgressSink — central progress-reporting module for host-side FSMs.

Provides a protocol type and two concrete sinks:

  stderr_progress_sink()  — writes one line per transition to stderr (or any
                            TextIO), flushing after every write.
  null_progress_sink()    — discards all events; useful in tests or when the
                            caller has no interest in progress lines.

Line format (left-padded fsm_name to 16 chars):

  [scenario-builder nginx_service_repair] DESIGN → LAUNCH_STAGING
  [user-proxy       nginx_service_repair turn=3] DECIDE → TOOL_EXEC (run_command)
  [benchmark        nginx_service_repair] evaluation_completed repair_success=True
"""
from __future__ import annotations

import sys
from typing import Callable, Protocol, TextIO

# Width to pad fsm_name to inside the bracket prefix.
# "scenario-builder" is 16 chars; "user-proxy" pads to match.
_FSM_NAME_WIDTH = 16


class FsmProgressSink(Protocol):
    """Callable protocol for FSM progress events.

    All parameters are keyword-only so callers can evolve the set without
    breaking existing sinks.
    """

    def __call__(self, *, fsm_name: str, scenario_name: str, details: dict) -> None: ...


def _format_line(fsm_name: str, scenario_name: str, details: dict) -> str:
    """Return a single formatted progress line.  Never raises."""
    try:
        padded = fsm_name.ljust(_FSM_NAME_WIDTH)

        # Build the bracket label: [fsm  scenario_name (turn=N if present)]
        label_parts = [padded, " ", scenario_name]
        turn = details.get("turn")
        if turn is not None:
            label_parts.append(f" turn={turn}")
        label = "".join(label_parts)

        from_state = details.get("from")
        to_state = details.get("to")

        if from_state is not None and to_state is not None:
            # State transition line
            transition = f"{from_state} \u2192 {to_state}"
            tool = details.get("tool")
            if tool:
                transition = f"{transition} ({tool})"
            return f"[{label}] {transition}"

        # Generic event line (e.g. benchmark-level events)
        event = details.get("event", "")
        extra_parts = []
        for key, value in details.items():
            if key in ("event", "turn", "from", "to"):
                continue
            extra_parts.append(f"{key}={value}")
        extra = " ".join(extra_parts)
        if extra:
            return f"[{label}] {event} {extra}".rstrip()
        return f"[{label}] {event}".rstrip()

    except Exception:  # noqa: BLE001 — never crash the caller
        return f"[progress format error] fsm={fsm_name!r} scenario={scenario_name!r}"


def stderr_progress_sink(stream: TextIO | None = None) -> FsmProgressSink:
    """Return a sink that writes one formatted line per event to *stream*.

    *stream* defaults to ``sys.stderr``.  The stream is flushed after every
    write, matching the ``_emit_progress`` pattern in ``backends/aws.py``.
    """
    _stream = stream  # capture; resolved lazily below if None

    def _sink(*, fsm_name: str, scenario_name: str, details: dict) -> None:
        target = _stream if _stream is not None else sys.stderr
        line = _format_line(fsm_name, scenario_name, details)
        try:
            print(line, file=target, flush=True)
        except Exception:  # noqa: BLE001 — never crash the caller
            pass

    return _sink  # type: ignore[return-value]


def null_progress_sink() -> FsmProgressSink:
    """Return a sink that discards all events."""

    def _sink(*, fsm_name: str, scenario_name: str, details: dict) -> None:  # noqa: ARG001
        pass

    return _sink  # type: ignore[return-value]
