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
    assert line == "[scenario-builder nginx_service_repair] DESIGN \u2192 LAUNCH_STAGING"


def test_format_line_fsm_name_padded_to_16() -> None:
    """Both scenario-builder (16) and user-proxy (10) are padded/trimmed to width."""
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
    # Both lines should have the same bracket-label width up to the closing ]
    bracket_sb = line_sb.split("]")[0] + "]"
    bracket_up = line_up.split("]")[0] + "]"
    # Lengths differ because scenario_name is the same and turn is absent in both;
    # the padded fsm_name sections should both be exactly 16 chars wide.
    assert "scenario-builder" in bracket_sb  # full name fits exactly
    assert "user-proxy      " in bracket_up  # padded to 16


def test_format_line_with_turn() -> None:
    line = _format_line(
        "user-proxy",
        "nginx_service_repair",
        {"from": "DECIDE", "to": "TOOL_EXEC", "turn": 3},
    )
    assert "turn=3" in line
    assert "DECIDE \u2192 TOOL_EXEC" in line


def test_format_line_with_tool() -> None:
    line = _format_line(
        "user-proxy",
        "nginx_service_repair",
        {"from": "DECIDE", "to": "TOOL_EXEC", "turn": 3, "tool": "run_command"},
    )
    assert "(run_command)" in line
    assert "turn=3" in line


def test_format_line_benchmark_event() -> None:
    line = _format_line(
        "benchmark",
        "nginx_service_repair",
        {"event": "evaluation_completed", "repair_success": True},
    )
    assert "evaluation_completed" in line
    assert "repair_success=True" in line
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
    assert "[scenario-builder nginx_service_repair] DESIGN \u2192 LAUNCH_STAGING" in output


def test_stderr_sink_writes_with_turn() -> None:
    buf = io.StringIO()
    sink = stderr_progress_sink(stream=buf)
    sink(
        fsm_name="user-proxy",
        scenario_name="nginx_service_repair",
        details={"from": "DECIDE", "to": "TOOL_EXEC", "turn": 3, "tool": "run_command"},
    )
    output = buf.getvalue()
    assert "turn=3" in output
    assert "(run_command)" in output


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
    assert "evaluation_completed" in output
    assert "repair_success=True" in output
