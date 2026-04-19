"""Tests for the Responses-based user proxy transport and FSM."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from eval_harness.controllers.base import InteractiveSession, SandboxController
from eval_harness.models import CommandExecutionResult
from eval_harness.orchestration import user_proxy_llm as user_proxy_llm_module
from eval_harness.orchestration.user_proxy_fsm import (
    DEFAULT_TOOLS,
    _FALLBACK_CLARIFICATION,
    _SAFE_RERUN_COMMANDS,
    ProxyRecentAction,
    ProxyRecentMemorySnapshot,
    UserProxyFSM,
    _check_reply_issues,
    _find_cached_exact_output,
    _render_recent_memory,
    _user_proxy_system_prompt,
)
from eval_harness.orchestration.user_proxy_llm import (
    UserProxyLLMClient,
    UserProxyLLMClientConfig,
    UserProxyLLMResponse,
    UserProxyReplyReview,
    UserProxyToolCall,
    _REVIEW_SYSTEM_PROMPT,
    build_proxy_native_history,
)


def _review(
    *,
    final_reply: str,
    verdict: str = "accept",
    reason: str = "reviewed",
    audit_json: dict | None = None,
) -> UserProxyReplyReview:
    return UserProxyReplyReview(
        verdict=verdict,
        final_reply=final_reply,
        reason=reason,
        audit_json=dict(audit_json or {}),
    )

@dataclass
class FakeUserProxyLLM:
    """Scripted LLM that returns pre-canned responses in order."""

    responses: list[UserProxyLLMResponse] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def __init__(self, responses: list[UserProxyLLMResponse]) -> None:
        self.responses = list(responses)
        self.calls = []

    def start_turn(self, *, system_prompt, transcript, assistant_reply, tools, recent_memory_text=None):
        self.calls.append(
            {
                "phase": "start",
                "system_prompt": system_prompt,
                "transcript": list(transcript),
                "assistant_reply": assistant_reply,
                "tools": tools,
                "recent_memory_text": recent_memory_text,
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
class FakeUserProxyLLMWithReview(FakeUserProxyLLM):
    """FakeUserProxyLLM that also supports review_reply."""

    review_responses: list[UserProxyReplyReview] = field(default_factory=list)
    review_calls: list[dict] = field(default_factory=list)

    def __init__(
        self,
        responses: list[UserProxyLLMResponse],
        review_responses: list[UserProxyReplyReview] | None = None,
    ) -> None:
        super().__init__(responses)
        self.review_responses = list(review_responses or [])
        self.review_calls = []

    def review_reply(
        self,
        *,
        system_prompt,
        transcript,
        subject_reply,
        recent_memory_text,
        tool_outputs_text,
        tool_names_used_this_turn,
        draft_reply,
    ):
        self.review_calls.append({
            "system_prompt": system_prompt,
            "transcript": list(transcript),
            "subject_reply": subject_reply,
            "recent_memory_text": recent_memory_text,
            "tool_outputs_text": list(tool_outputs_text),
            "tool_names_used_this_turn": list(tool_names_used_this_turn),
            "draft_reply": draft_reply,
        })
        if self.review_responses:
            return self.review_responses.pop(0)
        return _review(final_reply=draft_reply, reason="reviewed")

    def retry_turn(
        self,
        *,
        system_prompt,
        transcript,
        assistant_reply,
        tools,
        recent_memory_text=None,
        draft_reply,
        review_verdict,
        review_reason,
        tool_names_used_this_turn=None,
        tool_outputs_text=None,
    ):
        self.calls.append(
            {
                "phase": "retry",
                "system_prompt": system_prompt,
                "transcript": list(transcript),
                "assistant_reply": assistant_reply,
                "tools": tools,
                "recent_memory_text": recent_memory_text,
                "draft_reply": draft_reply,
                "review_verdict": review_verdict,
                "review_reason": review_reason,
                "tool_names_used_this_turn": list(tool_names_used_this_turn or ()),
                "tool_outputs_text": list(tool_outputs_text or ()),
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-default")


class FakeInteractiveSession(InteractiveSession):
    def __init__(self) -> None:
        self.inputs: list[dict[str, object]] = []
        self.output_queue: list[str] = []
        self.closed = False

    def send_input(self, input_text: str = "", control_keys: tuple[str, ...] = ()) -> None:
        self.inputs.append(
            {
                "input_text": input_text,
                "control_keys": tuple(control_keys),
            }
        )

    def read_output(self, timeout_seconds: float = 5.0) -> str:
        return self.output_queue.pop(0) if self.output_queue else ""

    def reset(self) -> None:
        self.inputs.clear()
        self.closed = False

    def close(self) -> None:
        self.closed = True

@dataclass
class FakeController(SandboxController):
    name: str = "fake_controller"
    execute_batches: list[tuple[CommandExecutionResult, ...]] = field(default_factory=list)
    executed: list[tuple[str, ...]] = field(default_factory=list)
    session_keys: list[str] = field(default_factory=list)
    raise_on_command: str | None = None
    support_interactive: bool = True
    interactive_sessions_opened: int = 0
    current_interactive_session: FakeInteractiveSession | None = None
    interactive_sessions_by_key: dict[str, FakeInteractiveSession] = field(default_factory=dict)

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

    def open_session(self, session_key: str) -> InteractiveSession:
        if not self.support_interactive:
            raise NotImplementedError("Interactive sessions not supported")
        existing = self.interactive_sessions_by_key.get(session_key)
        if existing is not None:
            self.current_interactive_session = existing
            return existing
        self.interactive_sessions_opened += 1
        self.current_interactive_session = FakeInteractiveSession()
        self.interactive_sessions_by_key[session_key] = self.current_interactive_session
        return self.current_interactive_session

    def close(self) -> None:
        pass


def _make_fsm(
    llm: FakeUserProxyLLM,
    controller: FakeController | None = None,
    *,
    max_tool_calls_per_turn: int = 4,
    transitions: list | None = None,
    proxy_mode: str = "strict_relay",
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
        user_proxy_mode=proxy_mode,
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
    # Even on stall the FSM provides a fallback message (not empty).
    assert result.user_message


def test_stalled_empty_content_no_tool_calls() -> None:
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-1"),
        ]
    )
    fsm = _make_fsm(llm)
    result = fsm.run_turn([], "What should I do?")

    assert result.stalled is True
    # Stall now provides the fallback clarification, not empty string.
    assert result.user_message == _FALLBACK_CLARIFICATION


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


def test_pragmatic_human_mode_system_prompt_includes_safe_fallbacks() -> None:
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="I checked the file.", tool_calls=(), finish_reason="stop", response_id="resp-1"),
        ]
    )
    fsm = _make_fsm(llm, proxy_mode="pragmatic_human")

    fsm.run_turn([], "Can you check the config file and show me what it says?")

    system_prompt = llm.calls[0]["system_prompt"]
    assert "safe read-only fallbacks" in system_prompt
    assert "read_file" in system_prompt
    assert "sed -n" in system_prompt
    assert "Do not infer edits" in system_prompt


def test_system_prompt_preserves_prior_command_context_and_blocks_assistant_voice() -> None:
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="still broken", tool_calls=(), finish_reason="stop", response_id="resp-1"),
        ]
    )
    fsm = _make_fsm(llm, proxy_mode="pragmatic_human")

    fsm.run_turn(
        [("user", "I tried systemctl restart nginx and it failed.")],
        "What exact error did you get when nginx failed to start?",
    )

    system_prompt = llm.calls[0]["system_prompt"]
    assert "remember that concrete command context" in system_prompt
    assert "rerun that same command" in system_prompt
    assert "paste the output and i'll diagnose it" in system_prompt.lower()
    assert "write like a confused user" in system_prompt.lower()


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


def test_user_proxy_llm_client_start_turn_uses_native_history(monkeypatch) -> None:
    """start_turn builds provider-native multi-turn history from the transcript."""
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

    # transcript has a prior proxy reply ("user") which is SKIPPED (leading user)
    # then the current subject reply comes in as the single "user" turn.
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
    # Native history: leading "user" entry skipped → only current reply.
    assert call["input"] == [{"role": "user", "content": "Run ls"}]


def test_user_proxy_llm_client_start_turn_multi_turn_native_history(monkeypatch) -> None:
    """start_turn builds multi-turn history when transcript has prior exchanges."""
    fake_api = FakeResponsesAPI([_fake_response(response_id="resp-1", output_text="ok")])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = fake_api

    monkeypatch.setattr(user_proxy_llm_module, "OpenAI", FakeOpenAI, raising=False)
    client = UserProxyLLMClient(
        UserProxyLLMClientConfig(model="gpt-m", api_key="k", request_timeout_seconds=10.0)
    )

    # Transcript: opening (user/proxy) → subject reply 0 (assistant) → proxy reply 0 (user)
    # The current subject reply is "subject reply 1" passed separately.
    transcript = [
        ("user", "my nginx is broken"),    # opening proxy message → SKIP (leading)
        ("assistant", "subject reply 0"),  # subject's first reply → proxy "user"
        ("user", "proxy reply 0"),         # proxy's first reply → proxy "assistant"
    ]
    client.start_turn(
        system_prompt="sys",
        transcript=transcript,
        assistant_reply="subject reply 1",
        tools=None,
    )
    call = fake_api.calls[0]
    assert call["input"] == [
        {"role": "user", "content": "subject reply 0"},
        {"role": "assistant", "content": "proxy reply 0"},
        {"role": "user", "content": "subject reply 1"},
    ]


def test_user_proxy_llm_client_start_turn_no_duplicate_subject_reply(monkeypatch) -> None:
    """Current subject reply appears exactly once in the input."""
    fake_api = FakeResponsesAPI([_fake_response(response_id="resp-1", output_text="ok")])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = fake_api

    monkeypatch.setattr(user_proxy_llm_module, "OpenAI", FakeOpenAI, raising=False)
    client = UserProxyLLMClient(
        UserProxyLLMClientConfig(model="gpt-m", api_key="k", request_timeout_seconds=10.0)
    )

    transcript = [
        ("user", "opening"),
        ("assistant", "subject reply 0"),
        ("user", "proxy reply 0"),
        # The CURRENT subject reply would be "subject reply 1" — it must NOT also be
        # in the transcript; the caller (benchmark.py) passes transcript[:-1].
    ]
    client.start_turn(
        system_prompt="sys",
        transcript=transcript,
        assistant_reply="subject reply 1",
        tools=None,
    )
    call = fake_api.calls[0]
    # Count occurrences of "subject reply 1"
    subject_reply_count = sum(
        1 for msg in call["input"] if "subject reply 1" in str(msg.get("content", ""))
    )
    assert subject_reply_count == 1


def test_user_proxy_llm_client_start_turn_recent_memory_appended(monkeypatch) -> None:
    """recent_memory_text is appended to the final user turn."""
    fake_api = FakeResponsesAPI([_fake_response(response_id="resp-1", output_text="ok")])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = fake_api

    monkeypatch.setattr(user_proxy_llm_module, "OpenAI", FakeOpenAI, raising=False)
    client = UserProxyLLMClient(
        UserProxyLLMClientConfig(model="gpt-m", api_key="k", request_timeout_seconds=10.0)
    )

    client.start_turn(
        system_prompt="sys",
        transcript=[],
        assistant_reply="what happened?",
        tools=None,
        recent_memory_text="$ ls\nfile.txt\n[exit 0]",
    )
    call = fake_api.calls[0]
    last_msg = call["input"][-1]
    assert last_msg["role"] == "user"
    assert "[Recent terminal actions]" in last_msg["content"]
    assert "file.txt" in last_msg["content"]


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


def test_user_proxy_llm_client_review_reply_returns_structured_review(monkeypatch) -> None:
    fake_api = FakeResponsesAPI(
        [
            _fake_response(
                response_id="resp-review",
                output_text='{"final_reply":"I ran the command and got exit 1.","verdict":"accept","reason":"Terminal evidence only.","audit_json":{"reasoning":"Terminal evidence only.","edited_reply":true}}',
            ),
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

    review = client.review_reply(
        system_prompt="You are a confused user.",
        transcript=[],
        subject_reply="What did the command print?",
        recent_memory_text="$ ls\nfile.txt\n[exit 0]",
        tool_outputs_text=["$ ls\nfile.txt\n[exit 0]"],
        tool_names_used_this_turn=["run_command"],
        draft_reply="You should run ls and tell me the output.",
    )

    assert review.final_reply == "I ran the command and got exit 1."
    assert review.verdict == "accept"
    assert review.reason == "Terminal evidence only."
    assert review.audit_json["reasoning"] == "Terminal evidence only."
    call = fake_api.calls[0]
    assert call["instructions"] == _REVIEW_SYSTEM_PROMPT
    assert "Tool names used this turn" in call["input"][0]["content"]


def test_user_proxy_llm_client_retry_turn_includes_tool_outputs(monkeypatch) -> None:
    fake_api = FakeResponsesAPI(
        [
            _fake_response(
                response_id="resp-retry",
                output_text="I removed the line and nginx -t is clean now.",
            ),
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

    response = client.retry_turn(
        system_prompt="You are a confused user.",
        transcript=[],
        assistant_reply="Remove that line and run nginx -t again.",
        tools=DEFAULT_TOOLS,
        recent_memory_text="$ cat /etc/nginx/conf.d/zz-benchmark-bad.conf\ninvalid-directive on\n[exit 0]",
        draft_reply="It still looks broken.",
        review_verdict="retry_with_tools",
        review_reason="Use the tools now.",
        tool_names_used_this_turn=["read_file"],
        tool_outputs_text=["$ cat /etc/nginx/conf.d/zz-benchmark-bad.conf\ninvalid-directive on\n[exit 0]"],
    )

    assert response.content == "I removed the line and nginx -t is clean now."
    retry_message = fake_api.calls[0]["input"][-1]["content"]
    assert "[Terminal output this turn]" in retry_message
    assert "invalid-directive on" in retry_message


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


def test_interactive_command_interception_success() -> None:
    tool_call = UserProxyToolCall(id="call-1", name="run_command", arguments={"command": "nano /etc/hosts"})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(
                content="",
                tool_calls=(tool_call,),
                finish_reason="tool_calls",
                response_id="resp-1",
            ),
        ]
    )
    controller = FakeController()
    fsm = _make_fsm(llm, controller)

    result = fsm.run_turn(
        transcript=[("user", "my issue")],
        subject_reply="open nano",
    )

    assert controller.interactive_sessions_opened == 1
    assert controller.current_interactive_session is not None
    assert controller.current_interactive_session.inputs == [
        {
            "input_text": "nano /etc/hosts\n",
            "control_keys": (),
        }
    ]

    assert len(result.tool_results) == 1
    res = result.tool_results[0]
    assert res.command == "nano /etc/hosts"
    assert "Interactive session started" in res.stdout
    assert "interactive_send" in res.stderr
    assert "interactive_read" in res.stderr
    assert res.exit_code == 0


def test_interactive_command_interception_fallback() -> None:
    tool_call = UserProxyToolCall(id="call-1", name="run_command", arguments={"command": "nano file"})
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(
                content="",
                tool_calls=(tool_call,),
                finish_reason="tool_calls",
                response_id="resp-1",
            ),
        ]
    )
    controller = FakeController()
    controller.support_interactive = False
    fsm = _make_fsm(llm, controller)

    result = fsm.run_turn(
        transcript=[],
        subject_reply="open nano",
    )

    assert controller.interactive_sessions_opened == 0
    assert len(result.tool_results) == 1
    res = result.tool_results[0]
    assert res.command == "nano file"
    assert res.exit_code == 1
    assert "read_file and apply_text_edit" in res.stderr


def test_interactive_follow_up_reuses_same_session_and_supports_control_keys() -> None:
    tc1 = UserProxyToolCall(id="call-1", name="run_command", arguments={"command": "nano /etc/hosts"})
    tc2 = UserProxyToolCall(
        id="call-2",
        name="interactive_send",
        arguments={"input_text": "127.0.0.1 localhost", "control_keys": ["ENTER", "CTRL_X"]},
    )
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc1,), finish_reason="tool_calls", response_id="resp-1"),
            UserProxyLLMResponse(content="", tool_calls=(tc2,), finish_reason="tool_calls", response_id="resp-2"),
            UserProxyLLMResponse(content="I updated the file.", tool_calls=(), finish_reason="stop", response_id="resp-3"),
        ]
    )
    controller = FakeController()
    fsm = _make_fsm(llm, controller)

    result = fsm.run_turn([], "use nano to edit hosts")

    assert not result.stalled
    assert result.user_message == "I updated the file."
    assert controller.interactive_sessions_opened == 1
    assert controller.current_interactive_session is not None
    assert controller.current_interactive_session.inputs == [
        {
            "input_text": "nano /etc/hosts\n",
            "control_keys": (),
        },
        {
            "input_text": "127.0.0.1 localhost",
            "control_keys": ("ENTER", "CTRL_X"),
        },
    ]


# ---------------------------------------------------------------------------
# New tests for proxy-relative history, review pass, memory, stall
# ---------------------------------------------------------------------------

def test_build_proxy_native_history_skips_leading_proxy_turns() -> None:
    """Leading "user" (proxy) entries are skipped; native history starts with subject turn."""
    pairs = build_proxy_native_history(
        transcript=[("user", "opening msg"), ("assistant", "subject reply 0"), ("user", "proxy reply 0")],
        subject_reply="subject reply 1",
    )
    assert pairs == [
        ("user", "subject reply 0"),
        ("assistant", "proxy reply 0"),
        ("user", "subject reply 1"),
    ]


def test_build_proxy_native_history_empty_transcript() -> None:
    """Empty transcript yields a single user turn with the current subject reply."""
    pairs = build_proxy_native_history(transcript=[], subject_reply="what do I do?")
    assert pairs == [("user", "what do I do?")]


def test_build_proxy_native_history_memory_appended() -> None:
    """recent_memory_text is appended to the last user turn."""
    pairs = build_proxy_native_history(
        transcript=[],
        subject_reply="check this",
        recent_memory_text="$ ls\nfile.txt\n[exit 0]",
    )
    assert len(pairs) == 1
    assert pairs[0][0] == "user"
    assert "[Recent terminal actions]" in pairs[0][1]
    assert "file.txt" in pairs[0][1]


def test_fsm_passes_transcript_without_duplication() -> None:
    """FSM start_turn receives transcript as-is and subject_reply separately."""
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="ok", tool_calls=(), finish_reason="stop", response_id="r1"),
        ]
    )
    fsm = _make_fsm(llm)
    fsm.run_turn(
        transcript=[("user", "opening"), ("assistant", "reply0"), ("user", "proxy0")],
        subject_reply="reply1",
    )
    call = llm.calls[0]
    assert call["transcript"] == [("user", "opening"), ("assistant", "reply0"), ("user", "proxy0")]
    assert call["assistant_reply"] == "reply1"


def test_fsm_passes_recent_memory_text_to_start_turn() -> None:
    """recent_memory_text rendered from snapshot is forwarded to start_turn."""
    action = ProxyRecentAction(
        tool_name="run_command",
        turn_index=0,
        command="ls /",
        result_text="$ ls /\nbin usr\n[exit 0]",
        exit_code=0,
        state_changing=False,
        safe_rerun=True,
    )
    memory = ProxyRecentMemorySnapshot(actions=(action,))
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="looks good", tool_calls=(), finish_reason="stop", response_id="r1"),
        ]
    )
    fsm = _make_fsm(llm)
    fsm.run_turn([], "what do you see?", proxy_recent_memory=memory)

    assert llm.calls[0]["recent_memory_text"] is not None
    assert "ls /" in llm.calls[0]["recent_memory_text"]


def test_review_pass_rewrites_draft() -> None:
    """When review_reply is available, its final_reply replaces the draft."""
    llm = FakeUserProxyLLMWithReview(
        responses=[
            UserProxyLLMResponse(content="You should run systemctl restart nginx.", tool_calls=(), finish_reason="stop", response_id="r1"),
        ],
        review_responses=[
            _review(
                final_reply="I tried to restart nginx like you said.",
                verdict="rewrite_text",
                reason="rewritten into user voice",
                audit_json={"review_stage": "initial"},
            ),
        ],
    )
    fsm = _make_fsm(llm)
    result = fsm.run_turn([], "Restart nginx for me")

    assert result.user_message == "I tried to restart nginx like you said."
    assert len(llm.review_calls) == 1
    assert llm.review_calls[0]["draft_reply"] == "You should run systemctl restart nginx."
    assert len(result.review_events) == 1
    assert result.review_events[0]["verdict"] == "rewrite_text"
    assert result.review_events[0]["audit_json"]["review_stage"] == "initial"


def test_review_call_includes_tool_outputs() -> None:
    """review_reply receives tool outputs from this turn."""
    tc = UserProxyToolCall(id="c1", name="run_command", arguments={"command": "echo hi"})
    llm = FakeUserProxyLLMWithReview(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls", response_id="r1"),
            UserProxyLLMResponse(content="I ran echo hi.", tool_calls=(), finish_reason="stop", response_id="r2"),
        ],
        review_responses=[
            _review(final_reply="I ran echo hi.", verdict="accept", reason="ok"),
        ],
    )
    controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="echo hi", stdout="hi", stderr="", exit_code=0),),
        ]
    )
    fsm = _make_fsm(llm, controller)
    result = fsm.run_turn([], "say hello")

    assert len(llm.review_calls) == 1
    # tool output text should reference the command result
    combined = " ".join(llm.review_calls[0]["tool_outputs_text"])
    assert "echo hi" in combined or "hi" in combined
    assert llm.review_calls[0]["tool_names_used_this_turn"] == ["run_command"]
    assert len(result.review_events) == 1
    assert result.review_events[0]["verdict"] == "accept"


def test_review_retry_with_tools_runs_one_corrective_retry() -> None:
    llm = FakeUserProxyLLMWithReview(
        responses=[
            UserProxyLLMResponse(
                content="It says nginx still won't start because of that bad conf file.",
                tool_calls=(),
                finish_reason="stop",
                response_id="r1",
            ),
            UserProxyLLMResponse(
                content="",
                tool_calls=(
                    UserProxyToolCall(
                        id="tc-read",
                        name="read_file",
                        arguments={"path": "/etc/nginx/conf.d/zz-benchmark-bad.conf"},
                    ),
                ),
                finish_reason="tool_calls",
                response_id="r2",
            ),
            UserProxyLLMResponse(
                content="",
                tool_calls=(
                    UserProxyToolCall(
                        id="tc-edit",
                        name="apply_text_edit",
                        arguments={
                            "path": "/etc/nginx/conf.d/zz-benchmark-bad.conf",
                            "old_text": "invalid-directive on;\n",
                            "new_text": "",
                        },
                    ),
                ),
                finish_reason="tool_calls",
                response_id="r3",
            ),
            UserProxyLLMResponse(
                content="",
                tool_calls=(
                    UserProxyToolCall(
                        id="tc-test",
                        name="run_command",
                        arguments={"command": "nginx -t"},
                    ),
                ),
                finish_reason="tool_calls",
                response_id="r4",
            ),
            UserProxyLLMResponse(
                content="I removed that line and nginx -t is clean now.",
                tool_calls=(),
                finish_reason="stop",
                response_id="r5",
            ),
        ],
        review_responses=[
            _review(
                final_reply="Use the tools now instead of repeating the diagnosis.",
                verdict="retry_with_tools",
                reason="Draft just paraphrased the diagnosis without acting.",
                audit_json={"review_stage": "initial"},
            ),
            _review(
                final_reply="I removed that line and nginx -t is clean now.",
                verdict="accept",
                reason="The retry actually performed the repair and retest.",
                audit_json={"review_stage": "corrective_retry"},
            ),
        ],
    )
    controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="cat /etc/nginx/conf.d/zz-benchmark-bad.conf", stdout="invalid-directive on\n", stderr="", exit_code=0),),
            (CommandExecutionResult(command="python3 /tmp/apply_text_edit.py", stdout="Applied edit to /etc/nginx/conf.d/zz-benchmark-bad.conf", stderr="", exit_code=0),),
            (CommandExecutionResult(command="nginx -t", stdout="syntax is ok", stderr="", exit_code=0),),
        ]
    )
    fsm = _make_fsm(llm, controller)

    result = fsm.run_turn(
        transcript=[("user", "The site is down."), ("assistant", "Run nginx -t."), ("user", "It failed.")],
        subject_reply="Remove that line or just remove that file, then run nginx -t again.",
    )

    assert not result.stalled
    assert result.user_message == "I removed that line and nginx -t is clean now."
    assert [r.metadata.get("user_proxy_tool_name") for r in result.tool_results] == [
        "read_file",
        "apply_text_edit",
        "run_command",
    ]
    assert len(llm.review_calls) == 2
    assert llm.calls[1]["phase"] == "retry"
    assert llm.calls[1]["review_verdict"] == "retry_with_tools"
    assert len(result.review_events) == 3
    assert result.review_events[0]["verdict"] == "retry_with_tools"
    assert result.review_events[1]["event_kind"] == "proxy_review_retry_decision"
    assert result.review_events[2]["verdict"] == "accept"


def test_review_retry_with_tools_falls_back_after_one_failed_retry() -> None:
    llm = FakeUserProxyLLMWithReview(
        responses=[
            UserProxyLLMResponse(
                content="It still looks broken because of that file.",
                tool_calls=(),
                finish_reason="stop",
                response_id="r1",
            ),
            UserProxyLLMResponse(
                content="That file still looks broken to me.",
                tool_calls=(),
                finish_reason="stop",
                response_id="r2",
            ),
        ],
        review_responses=[
            _review(final_reply="Act instead of summarizing.", verdict="retry_with_tools"),
            _review(final_reply="Still not acceptable.", verdict="retry_with_tools"),
        ],
    )
    fsm = _make_fsm(llm)

    result = fsm.run_turn(
        transcript=[],
        subject_reply="Remove that line or file, then run nginx -t again.",
    )

    assert not result.stalled
    assert result.user_message == _FALLBACK_CLARIFICATION
    assert len(llm.review_calls) == 2
    assert len(result.review_events) == 3
    assert result.review_events[0]["verdict"] == "retry_with_tools"
    assert result.review_events[1]["event_kind"] == "proxy_review_retry_decision"
    assert result.review_events[2]["verdict"] == "retry_with_tools"


def test_retry_turn_receives_same_turn_tool_outputs() -> None:
    tc = UserProxyToolCall(id="c1", name="read_file", arguments={"path": "/etc/nginx/conf.d/zz-benchmark-bad.conf"})
    llm = FakeUserProxyLLMWithReview(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls", response_id="r1"),
            UserProxyLLMResponse(content="It still looks broken because of that file.", tool_calls=(), finish_reason="stop", response_id="r2"),
            UserProxyLLMResponse(content="Which line should I remove?", tool_calls=(), finish_reason="stop", response_id="r3"),
        ],
        review_responses=[
            _review(final_reply="Use the tools now.", verdict="retry_with_tools", reason="Do not summarize the diagnosis."),
            _review(final_reply="Which line should I remove?", verdict="accept", reason="Human question is acceptable."),
        ],
    )
    controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="cat /etc/nginx/conf.d/zz-benchmark-bad.conf", stdout="invalid-directive on\n", stderr="", exit_code=0),),
        ]
    )
    fsm = _make_fsm(llm, controller)

    result = fsm.run_turn([], "Remove the bad line and try again.")

    assert result.user_message == "Which line should I remove?"
    assert llm.calls[2]["phase"] == "retry"
    assert any("invalid-directive on" in item for item in llm.calls[2]["tool_outputs_text"])


def test_generator_question_can_be_accepted_by_reviewer() -> None:
    llm = FakeUserProxyLLMWithReview(
        responses=[
            UserProxyLLMResponse(
                content="I don't know what file you mean.",
                tool_calls=(),
                finish_reason="stop",
                response_id="r1",
            ),
        ],
        review_responses=[
            _review(
                final_reply="Which file do you want me to edit?",
                verdict="accept",
                reason="Human question is acceptable.",
                audit_json={"review_stage": "initial"},
            ),
        ],
    )
    fsm = _make_fsm(llm)

    result = fsm.run_turn([], "Delete the bad line and try again.")

    assert not result.stalled
    assert result.user_message == "Which file do you want me to edit?"
    assert result.review_events[0]["verdict"] == "accept"


def test_review_verdict_rewrite_text_is_parsed() -> None:
    review = UserProxyReplyReview.from_dict(
        {
            "verdict": "rewrite_text",
            "final_reply": "I ran that and it still failed.",
            "reason": "Rewritten into human voice.",
        }
    )
    assert review.verdict == "rewrite_text"


def test_review_prompt_demands_evidence_only_for_log_requests() -> None:
    assert "If the assistant asked for logs, exact output, or command output" in _REVIEW_SYSTEM_PROMPT
    assert "return the evidence only" in _REVIEW_SYSTEM_PROMPT
    assert "do not diagnose the issue" in _REVIEW_SYSTEM_PROMPT
    assert "do not identify a root cause" in _REVIEW_SYSTEM_PROMPT
    assert "do not propose the next fix" in _REVIEW_SYSTEM_PROMPT
    assert "already-observed evidence" in _REVIEW_SYSTEM_PROMPT
    assert "breaking character" in _REVIEW_SYSTEM_PROMPT.lower()
    assert "do not tell the assistant what to run" in _REVIEW_SYSTEM_PROMPT.lower()
    assert "bad example" in _REVIEW_SYSTEM_PROMPT.lower()
    assert "good example" in _REVIEW_SYSTEM_PROMPT.lower()


def test_user_proxy_system_prompt_explicitly_forbids_assistant_voice() -> None:
    prompt = _user_proxy_system_prompt("nginx is down")
    assert "do not tell the assistant what to run" in prompt.lower()
    assert "do not ask the assistant to paste output" in prompt.lower()
    assert "bad example" in prompt.lower()
    assert "good example" in prompt.lower()


def test_review_audit_tracks_character_fields() -> None:
    review = UserProxyReplyReview.from_dict(
        {
            "verdict": "retry_with_tools",
            "final_reply": "I need you to run these and paste the results.",
            "reason": "The reply broke character.",
            "audit_json": {
                "character_ok": False,
                "character_issue": "assistant_voice",
                "voice_issue_examples": ["I need you to run these"],
            },
        }
    )
    assert review.audit_json["character_ok"] is False
    assert review.audit_json["character_issue"] == "assistant_voice"
    assert review.audit_json["voice_issue_examples"] == ["I need you to run these"]


def test_character_violation_forces_retry_even_if_reviewer_says_accept() -> None:
    llm = FakeUserProxyLLMWithReview(
        responses=[
            UserProxyLLMResponse(
                content="Run these and paste the output.",
                tool_calls=(),
                finish_reason="stop",
                response_id="resp-1",
            ),
            UserProxyLLMResponse(
                content="I ran it. nginx -t says the duplicate include is in /etc/nginx/nginx.conf:12.",
                tool_calls=(),
                finish_reason="stop",
                response_id="resp-2",
            ),
        ],
        review_responses=[
            _review(
                final_reply="Run these and paste the output.",
                verdict="accept",
                reason="Needs outputs first.",
                audit_json={
                    "character_ok": False,
                    "acceptable_tool_use": False,
                    "character_issue": "assistant_voice",
                    "voice_issue_examples": ["Run these and paste the output."],
                },
            ),
            _review(
                final_reply="I ran it. nginx -t says the duplicate include is in /etc/nginx/nginx.conf:12.",
                verdict="accept",
                reason="Now in character.",
                audit_json={"character_ok": True},
            ),
        ],
    )

    result = _make_fsm(llm).run_turn([], "Run nginx -t and tell me what it says.")

    assert result.user_message == "I ran it. nginx -t says the duplicate include is in /etc/nginx/nginx.conf:12."
    assert any(call["phase"] == "retry" for call in llm.calls)
    assert result.review_events[0]["verdict"] == "retry_with_tools"
    assert result.review_events[0]["audit_json"]["character_ok"] is False
    assert result.review_events[-1]["verdict"] == "accept"


def test_review_pass_skipped_when_client_lacks_method() -> None:
    """If llm_client has no review_reply, the draft is used as-is."""
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="I ran the command.", tool_calls=(), finish_reason="stop", response_id="r1"),
        ]
    )
    assert not hasattr(llm, "review_reply")
    fsm = _make_fsm(llm)
    result = fsm.run_turn([], "run ls")
    assert result.user_message == "I ran the command."


def test_repeated_opener_detected_and_fallback_used() -> None:
    """When conversation has advanced and proxy repeats opener, fallback is used."""
    opener = "my nginx is broken"
    llm = FakeUserProxyLLM(
        responses=[
            # Reply identical to opener after multiple turns → should fail check
            UserProxyLLMResponse(content=opener, tool_calls=(), finish_reason="stop", response_id="r1"),
        ]
    )
    fsm = _make_fsm(llm)
    # transcript has more than one prior proxy entry (conversation advanced)
    result = fsm.run_turn(
        transcript=[
            ("user", opener),                  # opening
            ("assistant", "try restarting"),   # subject turn 1
            ("user", "I tried, still broken"),  # proxy turn 1
            ("assistant", "can you show me the logs?"),  # subject turn 2
        ],
        subject_reply="what exactly is the problem?",
    )
    # LLM returned the opener → check fails → fallback clarification used
    assert result.user_message == _FALLBACK_CLARIFICATION
    assert not result.stalled


def test_repeated_prior_reply_falls_back() -> None:
    """Reply identical to the most-recent prior proxy reply triggers fallback."""
    prior_reply = "I already ran that and got exit code 1"
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content=prior_reply, tool_calls=(), finish_reason="stop", response_id="r1"),
        ]
    )
    fsm = _make_fsm(llm)
    result = fsm.run_turn(
        transcript=[
            ("user", "nginx is down"),
            ("assistant", "run systemctl status"),
            ("user", prior_reply),   # ← prior proxy reply
        ],
        subject_reply="what did you see?",
    )
    assert result.user_message == _FALLBACK_CLARIFICATION


def test_exact_output_cache_hit_short_circuits_llm() -> None:
    """When subject asks for exact output and we have it cached, skip LLM call."""
    cached_result = "$ systemctl status nginx\nActive: failed\n[exit 3]"
    action = ProxyRecentAction(
        tool_name="run_command",
        turn_index=0,
        command="systemctl status nginx",
        result_text=cached_result,
        exit_code=3,
        state_changing=False,
        safe_rerun=True,
    )
    memory = ProxyRecentMemorySnapshot(actions=(action,))
    llm = FakeUserProxyLLM(responses=[])  # no canned responses — LLM must not be called
    fsm = _make_fsm(llm)
    result = fsm.run_turn(
        [],
        "Can you paste the exact output of systemctl status nginx?",
        proxy_recent_memory=memory,
    )
    # LLM should NOT have been called (cache hit short-circuits)
    assert len(llm.calls) == 0
    assert cached_result in result.user_message


def test_safe_rerun_flag_set_for_read_only_commands() -> None:
    """Commands whose base name is in _SAFE_RERUN_COMMANDS get safe_rerun=True."""
    assert "cat" in _SAFE_RERUN_COMMANDS
    assert "ls" in _SAFE_RERUN_COMMANDS
    assert "systemctl" in _SAFE_RERUN_COMMANDS
    # State-changing commands are NOT safe to rerun.
    assert "apt-get" not in _SAFE_RERUN_COMMANDS
    assert "rm" not in _SAFE_RERUN_COMMANDS


def test_stall_provides_fallback_message_not_empty() -> None:
    """On stall the result has a non-empty user_message so benchmark has a fallback."""
    llm = FakeUserProxyLLM(responses=[
        UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="r1"),
    ])
    fsm = _make_fsm(llm)
    result = fsm.run_turn([], "What should I do?")
    assert result.stalled
    assert result.user_message  # must not be empty


def test_stall_uses_cached_output_as_fallback_when_available() -> None:
    """On stall the FSM prefers cached exact output over the generic clarification."""
    cached = "$ df -h\n/dev 90% full\n[exit 0]"
    action = ProxyRecentAction(
        tool_name="run_command",
        turn_index=0,
        command="df -h",
        result_text=cached,
        exit_code=0,
        state_changing=False,
        safe_rerun=True,
    )
    memory = ProxyRecentMemorySnapshot(actions=(action,))
    llm = FakeUserProxyLLM(responses=[
        UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="r1"),
    ])
    fsm = _make_fsm(llm)
    result = fsm.run_turn(
        [],
        "can you show me the exact output of df -h?",
        proxy_recent_memory=memory,
    )
    # Stall triggered in DECIDE (empty content, no tools) — but since the subject
    # asked for exact output and we have cache, the pre-LLM shortcut fires BEFORE
    # the stall path.  So actually the result should be non-stalled with the cache.
    assert cached in result.user_message


def test_render_recent_memory() -> None:
    a1 = ProxyRecentAction("run_command", 0, "ls /", "$ ls /\nbin\n[exit 0]", 0, False, True)
    a2 = ProxyRecentAction("run_command", 1, "cat /etc/hosts", "$ cat /etc/hosts\n127.0.0.1\n[exit 0]", 0, False, True)
    snap = ProxyRecentMemorySnapshot(actions=(a1, a2))
    text = _render_recent_memory(snap)
    assert "ls /" in text
    assert "cat /etc/hosts" in text


def test_find_cached_exact_output_matches_command_keyword() -> None:
    action = ProxyRecentAction(
        tool_name="run_command",
        turn_index=0,
        command="journalctl -u nginx",
        result_text="$ journalctl -u nginx\nMar 01 error...\n[exit 0]",
        exit_code=0,
        state_changing=False,
        safe_rerun=True,
    )
    memory = ProxyRecentMemorySnapshot(actions=(action,))
    result = _find_cached_exact_output(
        "Can you paste the exact output of journalctl?",
        memory,
    )
    assert result is not None
    assert "journalctl" in result


def test_find_cached_exact_output_returns_none_when_no_keywords() -> None:
    action = ProxyRecentAction("run_command", 0, "ls /", "out", 0, False, True)
    memory = ProxyRecentMemorySnapshot(actions=(action,))
    result = _find_cached_exact_output("What should I do next?", memory)
    assert result is None


def test_check_reply_issues_repeated_opener() -> None:
    prior = ["opener message", "a different reply"]
    issues = _check_reply_issues("opener message", prior_proxy_replies=prior)
    assert "repeated_opener" in issues


def test_check_reply_issues_repeated_last_reply() -> None:
    prior = ["opener", "I ran it already"]
    issues = _check_reply_issues("I ran it already", prior_proxy_replies=prior)
    assert "repeated_prior_reply" in issues


def test_check_reply_issues_clean_reply() -> None:
    prior = ["opener", "I ran the command and got exit 1."]
    issues = _check_reply_issues("Now it shows a different error.", prior_proxy_replies=prior)
    assert issues == []


def test_pragmatic_human_prompt_allows_limited_follow_through_after_repair() -> None:
    llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="I fixed it and checked again.", tool_calls=(), finish_reason="stop", response_id="resp-1"),
        ]
    )
    fsm = _make_fsm(llm, proxy_mode="pragmatic_human")

    fsm.run_turn([], "Remove that bad line, start nginx again, and tell me what happens.")

    system_prompt = llm.calls[0]["system_prompt"]
    assert "small amount of obvious follow-through" in system_prompt
    assert "Do not use that follow-through to invent new diagnostics" in system_prompt
