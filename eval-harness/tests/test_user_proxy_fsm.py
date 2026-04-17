"""Tests for UserProxyFSM (Phase 3)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from eval_harness.controllers.base import SandboxController
from eval_harness.models import CommandExecutionResult, VerificationCheck
from eval_harness.orchestration.user_proxy_fsm import (
    UserProxyFSM,
    UserProxyState,
    UserProxyTurnResult,
)
from eval_harness.orchestration.user_proxy_llm import (
    UserProxyLLMClient,
    UserProxyLLMClientConfig,
    UserProxyLLMResponse,
    UserProxyToolCall,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeUserProxyLLM(UserProxyLLMClient):
    """Scripted LLM that returns pre-canned responses in order."""

    responses: list[UserProxyLLMResponse] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def __init__(self, responses: list[UserProxyLLMResponse]) -> None:
        # Skip parent __init__ — we don't need a real requests.Session
        object.__setattr__(self, "__dict__", {})
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, *, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        if self.responses:
            return self.responses.pop(0)
        # Fallback: empty content, no tools — signals stall
        return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop")


@dataclass
class FakeController(SandboxController):
    name: str = "fake_controller"
    execute_batches: list[tuple[CommandExecutionResult, ...]] = field(default_factory=list)
    executed: list[tuple[str, ...]] = field(default_factory=list)
    session_keys: list[str] = field(default_factory=list)
    raise_on_command: str | None = None

    def execute_commands(
        self,
        commands: tuple[str, ...],
        *,
        agent_id: str = "",
        session_key: str | None = None,
    ) -> tuple[CommandExecutionResult, ...]:
        self.executed.append(commands)
        self.session_keys.append(session_key or "")
        if self.raise_on_command and any(self.raise_on_command in cmd for cmd in commands):
            raise RuntimeError(f"Simulated error for {commands}")
        if self.execute_batches:
            return self.execute_batches.pop(0)
        # Default: success with empty output
        return tuple(
            CommandExecutionResult(command=cmd, stdout="", stderr="", exit_code=0)
            for cmd in commands
        )

    def close(self) -> None:
        pass


def _make_fsm(
    llm: FakeUserProxyLLM,
    controller: FakeController | None = None,
    *,
    max_tool_calls_per_turn: int = 4,
    repair_checks: tuple[VerificationCheck, ...] = (),
    transitions: list | None = None,
) -> UserProxyFSM:
    if controller is None:
        controller = FakeController()
    captured = transitions

    def progress(fsm_name, scenario_name, details):
        if captured is not None:
            captured.append((details["from"], details["to"]))

    return UserProxyFSM(
        llm_client=llm,
        controller=controller,
        evaluation_run_id="eval-test-123",
        observable_problem_statement="nginx is down",
        repair_checks=repair_checks,
        verification_session_key="test-session",
        max_tool_calls_per_turn=max_tool_calls_per_turn,
        scenario_name="test-scenario",
        progress=progress,
    )


# ---------------------------------------------------------------------------
# Happy path: one tool call then reply
# ---------------------------------------------------------------------------


def test_happy_path_one_tool_call_then_reply() -> None:
    """LLM: tool call → exec result → final reply. Assert transcript and tool results."""
    tool_call = UserProxyToolCall(
        id="call-1",
        name="run_command",
        arguments={"command": "systemctl status nginx"},
    )
    llm = FakeUserProxyLLM(
        responses=[
            # DECIDE turn 1: emit a tool call
            UserProxyLLMResponse(
                content="",
                tool_calls=(tool_call,),
                finish_reason="tool_calls",
            ),
            # DECIDE turn 2: after seeing result, reply
            UserProxyLLMResponse(
                content="I ran the command. The service is failed.",
                tool_calls=(),
                finish_reason="stop",
            ),
        ]
    )
    controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl status nginx", stdout="failed", stderr="", exit_code=3),),
        ]
    )
    fsm = _make_fsm(llm, controller)

    result = fsm.run_turn(
        transcript=[("user", "nginx is down")],
        subject_reply="Please run systemctl status nginx",
    )

    assert not result.stalled
    assert not result.closure
    assert result.user_message == "I ran the command. The service is failed."
    assert len(result.tool_results) == 1
    assert result.tool_results[0].command == "systemctl status nginx"
    assert result.tool_results[0].exit_code == 3
    # The controller received the command
    assert ("systemctl status nginx",) in controller.executed


# ---------------------------------------------------------------------------
# Multi-tool-call loop
# ---------------------------------------------------------------------------


def test_multi_tool_call_loop() -> None:
    """LLM emits tool → result → another tool → result → final reply."""
    tc1 = UserProxyToolCall(id="c1", name="run_command", arguments={"command": "systemctl status nginx"})
    tc2 = UserProxyToolCall(id="c2", name="run_command", arguments={"command": "journalctl -u nginx -n 20"})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc1,), finish_reason="tool_calls"),
            UserProxyLLMResponse(content="", tool_calls=(tc2,), finish_reason="tool_calls"),
            UserProxyLLMResponse(content="Okay I see the issue.", tool_calls=(), finish_reason="stop"),
        ]
    )
    controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl status nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="journalctl -u nginx -n 20", stdout="Unit not found", stderr="", exit_code=1),),
        ]
    )
    fsm = _make_fsm(llm, controller)
    result = fsm.run_turn([], "Run diagnostics")

    assert not result.stalled
    assert len(result.tool_results) == 2
    assert result.user_message == "Okay I see the issue."


# ---------------------------------------------------------------------------
# Tool-call cap forces REPLY
# ---------------------------------------------------------------------------


def test_tool_call_cap_forces_reply() -> None:
    """After max_tool_calls_per_turn=2, the FSM transitions to REPLY."""
    tc = UserProxyToolCall(id="c1", name="run_command", arguments={"command": "echo test"})
    llm = FakeUserProxyLLM(
        responses=[
            # LLM keeps emitting tool calls
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls"),
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls"),
            # After cap → forced REPLY; the FSM will pick the last assistant content.
            # Since tool_call_count >= cap on the 3rd DECIDE, we go straight to REPLY.
            # But there's no content in the last assistant message — provide a final content.
            UserProxyLLMResponse(content="I've done enough commands.", tool_calls=(), finish_reason="stop"),
        ]
    )
    controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="echo test", stdout="test", stderr="", exit_code=0),),
            (CommandExecutionResult(command="echo test", stdout="test", stderr="", exit_code=0),),
        ]
    )
    # cap at 2 tool calls
    fsm = _make_fsm(llm, controller, max_tool_calls_per_turn=2)
    result = fsm.run_turn([], "Do some stuff")

    # Should have stopped after 2 tool calls
    assert len(result.tool_results) == 2


# ---------------------------------------------------------------------------
# Tool-call cap with no final content → stalled
# ---------------------------------------------------------------------------


def test_tool_call_cap_with_no_reply_stalls() -> None:
    """After cap, if there is no content in the message history, FSM stalls."""
    tc = UserProxyToolCall(id="c1", name="run_command", arguments={"command": "echo test"})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls"),
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls"),
            # Cap hit, no content → FSM goes to REPLY but finds empty content → STALLED
            # We need the cap-triggered REPLY to find empty content
        ]
    )
    controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="echo test", stdout="test", stderr="", exit_code=0),),
            (CommandExecutionResult(command="echo test", stdout="test", stderr="", exit_code=0),),
        ]
    )
    fsm = _make_fsm(llm, controller, max_tool_calls_per_turn=2)
    result = fsm.run_turn([], "Do something")

    assert result.stalled











# ---------------------------------------------------------------------------
# mark_task_complete
# ---------------------------------------------------------------------------

def test_mark_task_complete_calls_repair_checks_and_passes() -> None:
    tc = UserProxyToolCall(id="c1", name="mark_task_complete", arguments={})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls"),
            UserProxyLLMResponse(content="Looks like it is resolved.", tool_calls=(), finish_reason="stop"),
        ]
    )
    controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="curl -s http://localhost", stdout="Welcome to nginx!", stderr="", exit_code=0),),
        ]
    )
    check = VerificationCheck(
        name="nginx up",
        command="curl -s http://localhost",
        expected_substrings=("nginx",),
    )
    fsm = _make_fsm(llm, controller, repair_checks=(check,))
    result = fsm.run_turn([], "Is it fixed?")

    assert result.closure is True
    assert result.completion_claim_attempted is True
    assert result.completion_claim_passed is True
    assert result.completion_claim_report is not None
    assert result.completion_claim_report["all_passed"] is True

def test_mark_task_complete_with_failing_checks_returns_report() -> None:
    tc = UserProxyToolCall(id="c1", name="mark_task_complete", arguments={})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls"),
            UserProxyLLMResponse(content="Oh wait, it is still broken.", tool_calls=(), finish_reason="stop"),
        ]
    )
    controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="curl -s http://localhost", stdout="Connection refused", stderr="", exit_code=7),),
        ]
    )
    check = VerificationCheck(
        name="nginx up",
        command="curl -s http://localhost",
        expected_substrings=("nginx",),
    )
    fsm = _make_fsm(llm, controller, repair_checks=(check,))
    result = fsm.run_turn([], "Is it fixed?")

    assert result.closure is False
    assert result.completion_claim_attempted is True
    assert result.completion_claim_passed is False
    assert result.completion_claim_report is not None
    assert result.completion_claim_report["all_passed"] is False
    
    # Check that LLM received the report
    tool_message = llm.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert "FAILED: nginx up" in tool_message["content"]

def test_proxy_no_longer_closes_on_phrase() -> None:
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="all good, looks fixed now", tool_calls=(), finish_reason="stop"),
        ]
    )
    fsm = _make_fsm(llm)
    result = fsm.run_turn([], "How is it?")

    assert result.closure is False

# ---------------------------------------------------------------------------
# Stalled: empty content, no tool calls
# ---------------------------------------------------------------------------


def test_stalled_empty_content_no_tool_calls() -> None:
    """Proxy returns empty content with no tool calls → stalled=True."""
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop"),
        ]
    )
    fsm = _make_fsm(llm)
    result = fsm.run_turn([], "What should I do?")

    assert result.stalled is True
    assert result.closure is False


# ---------------------------------------------------------------------------
# Tool handler exception: continues without crashing
# ---------------------------------------------------------------------------


def test_tool_handler_exception_continues() -> None:
    """If execute_commands raises, FSM appends error as tool output and continues."""
    tc = UserProxyToolCall(id="c1", name="run_command", arguments={"command": "bad_command"})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls"),
            # After seeing error, LLM replies
            UserProxyLLMResponse(content="I got an error running that command.", tool_calls=(), finish_reason="stop"),
        ]
    )
    controller = FakeController(raise_on_command="bad_command")
    fsm = _make_fsm(llm, controller)
    result = fsm.run_turn([], "Run something")

    # FSM should not crash
    assert not result.stalled
    assert result.user_message == "I got an error running that command."
    # No tool results since command failed with exception
    assert len(result.tool_results) == 0


# ---------------------------------------------------------------------------
# Unknown tool name
# ---------------------------------------------------------------------------


def test_unknown_tool_name_appends_error_message() -> None:
    """FSM appends 'Unknown tool' error and continues when tool name is not in registry."""
    tc = UserProxyToolCall(id="c1", name="open_new_terminal", arguments={})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls"),
            UserProxyLLMResponse(content="Could not open terminal.", tool_calls=(), finish_reason="stop"),
        ]
    )
    fsm = _make_fsm(llm)
    result = fsm.run_turn([], "Open a new terminal")

    assert not result.stalled
    assert "Could not open terminal." in result.user_message


# ---------------------------------------------------------------------------
# State transitions are emitted via progress callback
# ---------------------------------------------------------------------------


def test_progress_callback_emitted() -> None:
    """Progress callback receives (from, to) state names on each transition."""
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="Okay I'll try that.", tool_calls=(), finish_reason="stop"),
        ]
    )
    transitions: list[tuple[str, str]] = []
    fsm = _make_fsm(llm, transitions=transitions)
    fsm.run_turn([], "What should I run?")

    assert ("READ_ASSISTANT", "DECIDE") in transitions
    assert ("DECIDE", "REPLY") in transitions
    assert ("REPLY", "DONE") in transitions


# ---------------------------------------------------------------------------
# SSM session key uses evaluation_run_id and terminal_id
# ---------------------------------------------------------------------------


def test_session_key_contains_eval_run_id() -> None:
    """execute_commands is called with session_key containing evaluation_run_id."""
    tc = UserProxyToolCall(id="c1", name="run_command", arguments={"command": "echo hi"})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls"),
            UserProxyLLMResponse(content="Done.", tool_calls=(), finish_reason="stop"),
        ]
    )
    controller = FakeController()
    fsm = UserProxyFSM(
        llm_client=llm,
        controller=controller,
        evaluation_run_id="eval-xyz",
        observable_problem_statement="test",
        repair_checks=(),
        verification_session_key="test-session",
        terminal_id="term-0",
    )
    fsm.run_turn([], "echo something")

    assert any("eval-xyz" in key for key in controller.session_keys)
    assert any("term-0" in key for key in controller.session_keys)






    

# ---------------------------------------------------------------------------
# Full UserProxyLLMClient dataclass shape (no network)
# ---------------------------------------------------------------------------


def test_user_proxy_llm_client_config() -> None:
    cfg = UserProxyLLMClientConfig(
        base_url="http://localhost:11434",
        model="llama3",
        api_key="test-key",
        request_timeout_seconds=30.0,
        max_output_tokens=512,
    )
    assert cfg.base_url == "http://localhost:11434"
    assert cfg.max_output_tokens == 512


def test_user_proxy_tool_call_dataclass() -> None:
    tc = UserProxyToolCall(id="tc-1", name="run_command", arguments={"command": "ls"})
    assert tc.id == "tc-1"
    assert tc.arguments["command"] == "ls"


def test_user_proxy_llm_response_dataclass() -> None:
    resp = UserProxyLLMResponse(content="hello", tool_calls=(), finish_reason="stop")
    assert resp.finish_reason == "stop"
    assert resp.tool_calls == ()
