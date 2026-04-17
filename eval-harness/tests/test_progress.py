"""Tests for orchestration/progress.py (Phase 4)."""
from __future__ import annotations

import io

import pytest

from eval_harness.orchestration.progress import (
    FsmProgressSink,
    _format_line,
    null_progress_sink,
    stderr_progress_sink,
)


# ---------------------------------------------------------------------------
# _format_line unit tests
# ---------------------------------------------------------------------------


def test_format_line_basic_transition() -> None:
    line = _format_line(
        "scenario-builder",
        "nginx_service_repair",
        {"from": "DESIGN", "to": "LAUNCH_STAGING"},
    )
    assert "[scenario-builder" in line
    assert "nginx_service_repair" in line
    assert "DESIGN \u2192 LAUNCH_STAGING" in line


def test_format_line_bracket_label_includes_context() -> None:
    line_sb = _format_line(
        "scenario-builder",
        "nginx_service_repair",
        {"from": "DESIGN", "to": "LAUNCH_STAGING"},
    )
    line_up = _format_line(
        "user-proxy",
        "nginx_service_repair",
        {"from": "DECIDE", "to": "TOOL_EXEC"},
    )
    bracket_sb = line_sb.split("]")[0] + "]"
    bracket_up = line_up.split("]")[0] + "]"
    assert bracket_sb.startswith("[scenario-builder")
    assert bracket_up.startswith("[user-proxy")
    assert "nginx_service_repair" in bracket_sb
    assert "nginx_service_repair" in bracket_up


def test_format_line_with_turn() -> None:
    line = _format_line(
        "user-proxy",
        "nginx_service_repair",
        {"from": "DECIDE", "to": "TOOL_EXEC", "turn": 3},
    )
    assert "T3" in line
    assert "Proxy calling tool:" in line


def test_format_line_with_tool() -> None:
    line = _format_line(
        "user-proxy",
        "nginx_service_repair",
        {"from": "DECIDE", "to": "TOOL_EXEC", "turn": 3, "tool": "run_command"},
    )
    assert "T3" in line
    assert "Proxy calling tool: run_command" in line


def test_format_line_benchmark_event() -> None:
    line = _format_line(
        "benchmark",
        "nginx_service_repair",
        {"event": "evaluation_completed", "repair_success": True},
    )
    assert "Evaluation PASSED" in line
    assert "nginx_service_repair" in line


def test_format_line_missing_from_to_no_crash() -> None:
    """Gracefully handle details with neither from/to nor event."""
    line = _format_line("benchmark", "nginx", {})
    # Should not crash; output is best-effort
    assert isinstance(line, str)


def test_format_line_completely_empty_details_no_crash() -> None:
    line = _format_line("scenario-builder", "", {})
    assert isinstance(line, str)


# ---------------------------------------------------------------------------
# stderr_progress_sink tests
# ---------------------------------------------------------------------------


def test_stderr_sink_writes_transition_line() -> None:
    buf = io.StringIO()
    sink = stderr_progress_sink(stream=buf)
    sink(
        fsm_name="scenario-builder",
        scenario_name="nginx_service_repair",
        details={"from": "DESIGN", "to": "LAUNCH_STAGING"},
    )
    output = buf.getvalue()
    assert "[scenario-builder" in output
    assert "DESIGN \u2192 LAUNCH_STAGING" in output


def test_stderr_sink_writes_with_turn() -> None:
    buf = io.StringIO()
    sink = stderr_progress_sink(stream=buf)
    sink(
        fsm_name="user-proxy",
        scenario_name="nginx_service_repair",
        details={"from": "DECIDE", "to": "TOOL_EXEC", "turn": 3, "tool": "run_command"},
    )
    output = buf.getvalue()
    assert "T3" in output
    assert "Proxy calling tool: run_command" in output


def test_stderr_sink_flush_after_each_write() -> None:
    """Ensure the stream is flushed (content is available immediately)."""

    class TrackingStream(io.StringIO):
        flush_count = 0

        def flush(self):
            self.flush_count += 1
            super().flush()

    buf = TrackingStream()
    sink = stderr_progress_sink(stream=buf)
    sink(fsm_name="scenario-builder", scenario_name="x", details={"from": "A", "to": "B"})
    assert buf.flush_count >= 1


def test_stderr_sink_multiple_calls_append() -> None:
    buf = io.StringIO()
    sink = stderr_progress_sink(stream=buf)
    sink(fsm_name="scenario-builder", scenario_name="x", details={"from": "DESIGN", "to": "LAUNCH_STAGING"})
    sink(fsm_name="scenario-builder", scenario_name="x", details={"from": "LAUNCH_STAGING", "to": "BUILD"})
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 2
    assert "DESIGN" in lines[0]
    assert "LAUNCH_STAGING" in lines[1]


def test_stderr_sink_missing_keys_no_crash() -> None:
    buf = io.StringIO()
    sink = stderr_progress_sink(stream=buf)
    # No from/to, no event — should not crash
    sink(fsm_name="benchmark", scenario_name="test", details={})
    # Should have written something
    assert isinstance(buf.getvalue(), str)


def test_stderr_sink_uses_sys_stderr_by_default(monkeypatch) -> None:
    """When stream=None, the sink writes to sys.stderr."""
    import sys

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    sink = stderr_progress_sink()  # no stream arg
    sink(fsm_name="scenario-builder", scenario_name="x", details={"from": "A", "to": "B"})
    assert "A \u2192 B" in buf.getvalue()


# ---------------------------------------------------------------------------
# null_progress_sink tests
# ---------------------------------------------------------------------------


def test_null_sink_produces_no_output() -> None:
    buf = io.StringIO()
    # null_sink ignores the stream entirely
    sink = null_progress_sink()
    sink(
        fsm_name="scenario-builder",
        scenario_name="nginx_service_repair",
        details={"from": "DESIGN", "to": "LAUNCH_STAGING"},
    )
    # Nothing written to buf (it was never passed in, and null_sink discards all)
    assert buf.getvalue() == ""


def test_null_sink_does_not_crash_on_any_input() -> None:
    sink = null_progress_sink()
    sink(fsm_name="x", scenario_name="y", details={})
    sink(fsm_name="", scenario_name="", details={"from": "A", "to": "B", "turn": 99})


# ---------------------------------------------------------------------------
# Protocol compatibility
# ---------------------------------------------------------------------------


def test_sinks_are_callable() -> None:
    assert callable(stderr_progress_sink())
    assert callable(null_progress_sink())


def test_stderr_sink_benchmark_event_line() -> None:
    buf = io.StringIO()
    sink = stderr_progress_sink(stream=buf)
    sink(
        fsm_name="benchmark",
        scenario_name="nginx_service_repair",
        details={"event": "evaluation_completed", "repair_success": True},
    )
    output = buf.getvalue()
    assert "Evaluation PASSED" in output
