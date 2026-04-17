"""Tests for the Responses-based user proxy transport and FSM."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from eval_harness.controllers.base import SandboxController
from eval_harness.models import CommandExecutionResult
from eval_harness.orchestration import user_proxy_llm as user_proxy_llm_module
from eval_harness.orchestration.user_proxy_fsm import DEFAULT_TOOLS, UserProxyFSM
from eval_harness.orchestration.user_proxy_llm import (
    UserProxyLLMClient,
    UserProxyLLMClientConfig,
    UserProxyLLMResponse,
    UserProxyToolCall,
)


@dataclass
class FakeUserProxyLLM:
    """Scripted LLM that returns pre-canned responses in order."""

    responses: list[UserProxyLLMResponse] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def __init__(self, responses: list[UserProxyLLMResponse]) -> None:
        self.responses = list(responses)
        self.calls = []

    def start_turn(self, *, system_prompt, transcript, assistant_reply, tools):
        self.calls.append(
            {
                "phase": "start",
                "system_prompt": system_prompt,
                "transcript": list(transcript),
                "assistant_reply": assistant_reply,
                "tools": tools,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-default")

    def continue_turn(self, *, system_prompt, previous_response_id, tool_outputs, tools):
        self.calls.append(
            {
                "phase": "continue",
                "system_prompt": system_prompt,
                "previous_response_id": previous_response_id,
                "tool_outputs": list(tool_outputs),
                "tools": tools,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-default")


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
    transitions: list | None = None,
) -> UserProxyFSM:
    if controller is None:
        controller = FakeController()
    captured = transitions

    def progress(fsm_name, scenario_name, details):
        if captured is not None and "from" in details and "to" in details:
            captured.append((details["from"], details["to"]))

    return UserProxyFSM(
        llm_client=llm,
        controller=controller,
        evaluation_run_id="eval-test-123",
        observable_problem_statement="nginx is down",
        max_tool_calls_per_turn=max_tool_calls_per_turn,
        scenario_name="test-scenario",
        progress=progress,
    )


class FakeResponsesAPI:
    def __init__(self, responses: list[object]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _fake_response(*, response_id: str, output_text: str = "", tool_calls: list[object] | None = None, status: str = "completed"):
    return SimpleNamespace(
        id=response_id,
        output_text=output_text,
        output=tool_calls or [],
        status=status,
        error=None,
        incomplete_details=None,
    )


def test_happy_path_one_tool_call_then_reply() -> None:
    tool_call = UserProxyToolCall(
        id="call-1",
        name="run_command",
        arguments={"command": "systemctl status nginx"},
    )
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(
                content="",
                tool_calls=(tool_call,),
                finish_reason="tool_calls",
                response_id="resp-1",
            ),
            UserProxyLLMResponse(
                content="I ran the command. The service is failed.",
                tool_calls=(),
                finish_reason="stop",
                response_id="resp-2",
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
    assert result.user_message == "I ran the command. The service is failed."
    assert len(result.tool_results) == 1
    assert result.tool_results[0].command == "systemctl status nginx"
    assert result.tool_results[0].exit_code == 3
    assert llm.calls[0]["phase"] == "start"
    assert llm.calls[1]["phase"] == "continue"
    assert llm.calls[1]["previous_response_id"] == "resp-1"
    assert llm.calls[1]["tool_outputs"] == [
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "name": "run_command",
            "output": "$ systemctl status nginx\nfailed\n[exit 3]",
        }
    ]
    assert ("systemctl status nginx",) in controller.executed


def test_multi_tool_call_loop() -> None:
    tc1 = UserProxyToolCall(id="c1", name="run_command", arguments={"command": "systemctl status nginx"})
    tc2 = UserProxyToolCall(id="c2", name="run_command", arguments={"command": "journalctl -u nginx -n 20"})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc1,), finish_reason="tool_calls", response_id="resp-1"),
            UserProxyLLMResponse(content="", tool_calls=(tc2,), finish_reason="tool_calls", response_id="resp-2"),
            UserProxyLLMResponse(content="Okay I see the issue.", tool_calls=(), finish_reason="stop", response_id="resp-3"),
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
    assert [call["phase"] for call in llm.calls] == ["start", "continue", "continue"]
    assert llm.calls[2]["previous_response_id"] == "resp-2"


def test_tool_call_cap_with_no_reply_stalls() -> None:
    tc = UserProxyToolCall(id="c1", name="run_command", arguments={"command": "echo test"})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls", response_id="resp-1"),
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls", response_id="resp-2"),
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


def test_stalled_empty_content_no_tool_calls() -> None:
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-1"),
        ]
    )
    fsm = _make_fsm(llm)
    result = fsm.run_turn([], "What should I do?")

    assert result.stalled is True


def test_tool_handler_exception_continues() -> None:
    tc = UserProxyToolCall(id="c1", name="run_command", arguments={"command": "bad_command"})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls", response_id="resp-1"),
            UserProxyLLMResponse(content="I got an error running that command.", tool_calls=(), finish_reason="stop", response_id="resp-2"),
        ]
    )
    controller = FakeController(raise_on_command="bad_command")
    fsm = _make_fsm(llm, controller)
    result = fsm.run_turn([], "Run something")

    assert not result.stalled
    assert result.user_message == "I got an error running that command."
    assert len(result.tool_results) == 0
    assert llm.calls[1]["tool_outputs"][0]["call_id"] == "c1"
    assert "Tool error:" in llm.calls[1]["tool_outputs"][0]["output"]


def test_unknown_tool_name_appends_error_message() -> None:
    tc = UserProxyToolCall(id="c1", name="open_new_terminal", arguments={})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls", response_id="resp-1"),
            UserProxyLLMResponse(content="Could not open terminal.", tool_calls=(), finish_reason="stop", response_id="resp-2"),
        ]
    )
    fsm = _make_fsm(llm)
    result = fsm.run_turn([], "Open a new terminal")

    assert not result.stalled
    assert "Could not open terminal." in result.user_message
    assert llm.calls[1]["tool_outputs"][0]["output"] == "Unknown tool: open_new_terminal"


def test_progress_callback_emitted() -> None:
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="Okay I'll try that.", tool_calls=(), finish_reason="stop", response_id="resp-1"),
        ]
    )
    transitions: list[tuple[str, str]] = []
    fsm = _make_fsm(llm, transitions=transitions)
    fsm.run_turn([], "What should I run?")

    assert ("READ_ASSISTANT", "DECIDE") in transitions
    assert ("DECIDE", "REPLY") in transitions
    assert ("REPLY", "DONE") in transitions


def test_session_key_contains_eval_run_id() -> None:
    tc = UserProxyToolCall(id="c1", name="run_command", arguments={"command": "echo hi"})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls", response_id="resp-1"),
            UserProxyLLMResponse(content="Done.", tool_calls=(), finish_reason="stop", response_id="resp-2"),
        ]
    )
    controller = FakeController()
    fsm = UserProxyFSM(
        llm_client=llm,
        controller=controller,
        evaluation_run_id="eval-xyz",
        observable_problem_statement="test",
        terminal_id="term-0",
    )
    fsm.run_turn([], "echo something")

    assert any("eval-xyz" in key for key in controller.session_keys)
    assert any("term-0" in key for key in controller.session_keys)


def test_user_proxy_llm_client_start_turn_uses_responses_api_shape(monkeypatch) -> None:
    fake_api = FakeResponsesAPI(
        [
            _fake_response(
                response_id="resp-start",
                tool_calls=[SimpleNamespace(type="function_call", call_id="call-1", name="run_command", arguments='{"command":"ls"}')],
            )
        ]
    )
    client_inits: list[dict] = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            client_inits.append(kwargs)
            self.responses = fake_api

    monkeypatch.setattr(user_proxy_llm_module, "OpenAI", FakeOpenAI, raising=False)
    client = UserProxyLLMClient(
        UserProxyLLMClientConfig(
            model="gpt-5.4-mini",
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            request_timeout_seconds=30.0,
            max_output_tokens=512,
            reasoning_effort="medium",
        )
    )

    response = client.start_turn(
        system_prompt="You are a confused user.",
        transcript=[("user", "nginx is down")],
        assistant_reply="Run ls",
        tools=DEFAULT_TOOLS,
    )

    assert client_inits == [
        {
            "api_key": "test-key",
            "base_url": "https://api.openai.com/v1",
            "timeout": 30.0,
        }
    ]
    assert response.response_id == "resp-start"
    assert response.tool_calls == (
        UserProxyToolCall(id="call-1", name="run_command", arguments={"command": "ls"}),
    )
    call = fake_api.calls[0]
    assert call["model"] == "gpt-5.4-mini"
    assert call["instructions"] == "You are a confused user."
    assert call["max_output_tokens"] == 512
    assert call["reasoning"] == {"effort": "medium"}
    assert call["parallel_tool_calls"] is True
    assert call["tools"][0]["parameters"]["additionalProperties"] is False
    assert call["input"] == [
        {
            "role": "user",
            "content": "Conversation so far:\nuser: nginx is down\n\nAssistant just said:\nRun ls",
        }
    ]


def test_default_run_command_tool_is_strict_object_schema() -> None:
    tool = DEFAULT_TOOLS[0]

    assert tool["name"] == "run_command"
    assert tool["strict"] is True
    assert tool["parameters"]["type"] == "object"
    assert tool["parameters"]["required"] == ["command"]
    assert tool["parameters"]["additionalProperties"] is False


def test_user_proxy_llm_client_rejects_strict_tools_without_closed_object_schema(monkeypatch) -> None:
    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponsesAPI([_fake_response(response_id="resp-unused")])

    monkeypatch.setattr(user_proxy_llm_module, "OpenAI", FakeOpenAI, raising=False)
    client = UserProxyLLMClient(
        UserProxyLLMClientConfig(
            model="gpt-5.4-mini",
            api_key="test-key",
            request_timeout_seconds=30.0,
        )
    )

    with pytest.raises(ValueError, match="additionalProperties"):
        client.start_turn(
            system_prompt="You are a confused user.",
            transcript=[],
            assistant_reply="Run ls",
            tools=[
                {
                    "type": "function",
                    "name": "run_command",
                    "description": "Run a command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                    "strict": True,
                }
            ],
        )


def test_user_proxy_llm_client_continue_turn_submits_function_outputs(monkeypatch) -> None:
    fake_api = FakeResponsesAPI(
        [
            _fake_response(response_id="resp-next", output_text="Done."),
        ]
    )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = fake_api

    monkeypatch.setattr(user_proxy_llm_module, "OpenAI", FakeOpenAI, raising=False)
    client = UserProxyLLMClient(
        UserProxyLLMClientConfig(
            model="gpt-5.4-mini",
            api_key="test-key",
            request_timeout_seconds=30.0,
        )
    )

    response = client.continue_turn(
        system_prompt="You are a confused user.",
        previous_response_id="resp-prev",
        tool_outputs=[{"type": "function_call_output", "call_id": "call-1", "name": "run_command", "output": "$ ls\nfile.txt\n[exit 0]"}],
        tools=[],
    )

    assert response.response_id == "resp-next"
    assert response.content == "Done."
    assert fake_api.calls[0]["previous_response_id"] == "resp-prev"
    assert fake_api.calls[0]["input"] == [
        {"type": "function_call_output", "call_id": "call-1", "name": "run_command", "output": "$ ls\nfile.txt\n[exit 0]"}
    ]


def test_user_proxy_llm_client_config() -> None:
    cfg = UserProxyLLMClientConfig(
        base_url="http://localhost:11434",
        model="llama3",
        api_key="test-key",
        request_timeout_seconds=30.0,
        max_output_tokens=512,
        reasoning_effort="medium",
    )
    assert cfg.base_url == "http://localhost:11434"
    assert cfg.max_output_tokens == 512
    assert cfg.reasoning_effort == "medium"


def test_user_proxy_tool_call_dataclass() -> None:
    tc = UserProxyToolCall(id="tc-1", name="run_command", arguments={"command": "ls"})
    assert tc.id == "tc-1"
    assert tc.arguments["command"] == "ls"


def test_user_proxy_llm_response_dataclass() -> None:
    resp = UserProxyLLMResponse(content="hello", tool_calls=(), finish_reason="stop", response_id="resp-1")
    assert resp.finish_reason == "stop"
    assert resp.response_id == "resp-1"
    assert resp.tool_calls == ()
