"""FsmProgressSink — central progress-reporting module for host-side FSMs.

Provides a protocol type and two concrete sinks:

  stderr_progress_sink()  — writes one human-readable line per event to stderr.
  null_progress_sink()    — discards all events; useful in tests.

Example output:

  [benchmark  nginx_service_repair/regular]  T0  Sending message to subject (142 chars)
  [benchmark  nginx_service_repair/regular]  T0  Waiting for subject to respond...
  [benchmark  nginx_service_repair/regular]  T0  Subject: "Let's check the service. Run: systemctl status nginx"
  [benchmark  nginx_service_repair/regular]  T0  Running 2 repair check(s)...
  [benchmark  nginx_service_repair/regular]  T0  Repair checks: FAILED
  [user-proxy nginx_service_repair/regular]  T0  Proxy LLM thinking... (3 messages in context)
  [user-proxy nginx_service_repair/regular]  T0  Proxy LLM done — 1 tool call(s), finish=tool_calls
  [user-proxy nginx_service_repair/regular]  T0  Proxy running: systemctl status nginx
  [benchmark  nginx_service_repair/regular]      Evaluation FAILED — proxy_stalled
"""
from __future__ import annotations

import sys
from typing import Callable, Protocol, TextIO

_LABEL_WIDTH = 36  # width of the "[fsm  scenario/subject]" bracket block


class FsmProgressSink(Protocol):
    """Callable protocol for FSM progress events.

    All parameters are keyword-only so callers can evolve the set without
    breaking existing sinks.
    """

    def __call__(self, *, fsm_name: str, scenario_name: str, details: dict) -> None: ...


def _turn_tag(details: dict) -> str:
    turn = details.get("turn")
    return f"T{turn}" if turn is not None else ""


def _render_message(fsm_name: str, scenario_name: str, details: dict) -> str:
    """Return the human-readable body of the progress line."""
    event = details.get("event")
    from_state = details.get("from")
    to_state = details.get("to")

    # ------------------------------------------------------------------ #
    # State transitions (user-proxy FSM)                                   #
    # ------------------------------------------------------------------ #
    if from_state is not None and to_state is not None:
        tool = details.get("tool", "")
        command = details.get("command", "")
        reason = details.get("reason", "")

        if to_state == "TOOL_EXEC":
            if tool == "run_command" and command:
                return f"Proxy running: {command}"
            if tool == "mark_task_complete":
                snippet = (reason[:80] + "…") if len(reason) > 80 else reason
                return f"Proxy claiming task complete: \"{snippet}\""
            return f"Proxy calling tool: {tool}"

        if to_state == "REPLY":
            return "Proxy composing reply to user"
        if to_state == "DONE":
            return "Proxy turn complete"
        if to_state == "STALLED":
            return "Proxy stalled — no decision made"
        if from_state == "TOOL_EXEC" and to_state == "DECIDE":
            return "Tool executed — proxy deciding next step"

        if from_state == "READ_ASSISTANT" and to_state == "DECIDE":
            return "Reading subject's reply, deciding how to respond..."

        return f"{from_state} → {to_state}"

    # ------------------------------------------------------------------ #
    # Named events                                                          #
    # ------------------------------------------------------------------ #

    # benchmark events
    if event == "user_turn_start":
        chars = details.get("msg_len", "?")
        return f"Sending message to subject ({chars} chars)"
    if event == "subject_wait":
        return "Waiting for subject to respond..."
    if event == "subject_replied":
        snippet = details.get("reply_snippet", "")
        return f"Subject: \"{snippet}\""
    if event == "repair_check_start":
        n = details.get("check_count", "?")
        return f"Running {n} repair check(s)..."
    if event == "repair_check_done":
        passed = details.get("passed")
        outcome = "PASSED ✓" if passed else "FAILED ✗"
        return f"Repair checks: {outcome}"
    if event == "evaluation_completed":
        success = details.get("repair_success")
        reason = details.get("reason", "")
        outcome = "PASSED ✓" if success else "FAILED ✗"
        suffix = f" — {reason}" if reason else ""
        return f"Evaluation {outcome}{suffix}"

    # user-proxy events
    if event == "llm_wait":
        n = details.get("msg_count", "?")
        return f"Proxy LLM thinking... ({n} messages in context)"
    if event == "llm_done":
        tc = details.get("tool_calls", 0)
        finish = details.get("finish_reason", "?")
        if tc:
            return f"Proxy LLM done \u2014 {tc} tool call(s), finish={finish}"
        return f"Proxy LLM done \u2014 text reply, finish={finish}"

    if event == "planner_thinking_start":
        phase = str(details.get("phase", "planner"))
        return f"Planner thinking: {phase}"
    if event == "planner_thinking_done":
        phase = str(details.get("phase", "planner"))
        elapsed = details.get("elapsed_seconds", "?")
        return f"Planner done: {phase} ({elapsed}s)"

    # scenario-builder or other FSMs: fall back to a clean key=value line
    _SKIP = {"event", "turn", "from", "to", "subject_name", "tool_call_count"}
    parts = [event] if event else []
    for k, v in details.items():
        if k not in _SKIP:
            parts.append(f"{k}={v}")
    return "  ".join(parts)


def _format_line(fsm_name: str, scenario_name: str, details: dict) -> str:
    """Return a single human-readable progress line.  Never raises."""
    try:
        subject_name = details.get("subject_name", "")
        context = f"{scenario_name}/{subject_name}" if subject_name else scenario_name
        label = f"[{fsm_name}  {context}]".ljust(_LABEL_WIDTH)

        body = _render_message(fsm_name, scenario_name, details)
        if not body:
            return ""

        turn_tag = _turn_tag(details)
        if turn_tag:
            return f"{label}  {turn_tag:<4}  {body}"
        return f"{label}        {body}"

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
        if not line:
            return
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
