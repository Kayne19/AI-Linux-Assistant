"""UserProxyFSM — host-side FSM that drives the per-turn proxy loop.

Each turn the FSM:
  READ_ASSISTANT → DECIDE → (TOOL_EXEC → DECIDE)* → REPLY → DONE
                                                   → STALLED

All commands go through SandboxController.execute_commands() (SSM or fake
in tests).  No controller.send() is used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

from ..controllers.base import SandboxController
from ..models import CommandExecutionResult
from .user_proxy_llm import UserProxyLLMClient, UserProxyLLMResponse, UserProxyToolCall


# ---------------------------------------------------------------------------
# Closure detection (mirrors _PROXY_CLOSURE_RE from the old benchmark.py)
# ---------------------------------------------------------------------------

_PROXY_CLOSURE_RE = re.compile(
    r"\b("
    r"thanks|thank you|all good|looks good|working now|works now|fixed|resolved|that fixed it|problem solved|we're good"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Tool schema registry
# ---------------------------------------------------------------------------

# Phase 3 ships only run_command.  Multi-terminal is a planned extension —
# open_new_terminal() would allocate a fresh SSM session keyed by terminal_id.
_RUN_COMMAND_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": (
            "Run a shell command on the user's Linux machine. "
            "Use this only when the assistant explicitly asked you to run something specific. "
            "Returns stdout, stderr, and exit code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Exact shell command to run",
                }
            },
            "required": ["command"],
        },
    },
}

DEFAULT_TOOLS: list[dict[str, Any]] = [_RUN_COMMAND_TOOL]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _user_proxy_system_prompt(observable_problem_statement: str) -> str:
    return (
        "You are a frustrated human user at a Linux terminal. You do not know why your machine is broken or what caused it.\n\n"
        f"Your situation: {observable_problem_statement}\n\n"
        "Rules:\n"
        "- Stay in character as a non-expert user who has shell access but limited Linux knowledge.\n"
        "- When the assistant asks you to run a command, use the run_command tool to run it and relay the output.\n"
        "- Only run commands the assistant explicitly asked you to run. Do not invent diagnostics.\n"
        "- Relay the exact command the assistant requested. Do not add sudo, extra flags, extra subcommands, or a more specific variant on your own.\n"
        "- Do not combine multiple commands unless the assistant explicitly requested multiple separate commands.\n"
        "- If the assistant did not give an exact command, ask what exact command to run instead of guessing.\n"
        "- Never fabricate command output. Never say 'Fixed' or 'Done' unless you have seen actual output confirming repair.\n"
        "- Do not write like an AI assistant. Write like a confused user.\n"
        "- When you have observed output that confirms the problem stated above is resolved, reply with exactly: REPAIR_CONFIRMED"
    )


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


class UserProxyState(Enum):
    READ_ASSISTANT = auto()
    DECIDE = auto()
    TOOL_EXEC = auto()
    REPLY = auto()
    DONE = auto()
    STALLED = auto()


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class UserProxyContext:
    transcript: list[tuple[str, str]]  # cumulative (role, content) pairs
    assistant_reply: str
    observable_problem_statement: str
    messages: list[dict[str, Any]]  # OpenAI-format message array for the proxy LLM
    tool_call_count: int = 0
    tool_results: list[CommandExecutionResult] = field(default_factory=list)
    final_reply: str = ""
    closure: bool = False
    stalled: bool = False
    # Pending tool calls from last assistant message (set in DECIDE, consumed in TOOL_EXEC)
    _pending_tool_calls: list[UserProxyToolCall] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserProxyTurnResult:
    user_message: str
    tool_results: tuple[CommandExecutionResult, ...]
    closure: bool
    stalled: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_tool_result(result: CommandExecutionResult) -> str:
    stdout = (result.stdout or "").strip()[:2000]
    stderr = (result.stderr or "").strip()[:2000]
    exit_code = result.exit_code
    parts = [f"$ {result.command}"]
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    parts.append(f"[exit {exit_code}]")
    return "\n".join(parts)


def _looks_like_closure_reply(reply: str) -> bool:
    stripped = reply.strip()
    if not stripped:
        return False
    return bool(_PROXY_CLOSURE_RE.search(stripped))


# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------


class UserProxyFSM:
    """Tool-calling FSM that simulates a confused human user.

    Constructor args:
        llm_client          UserProxyLLMClient to drive proxy turns.
        controller          SandboxController — commands go through execute_commands.
        evaluation_run_id   Used as part of the SSM session key.
        observable_problem_statement  Injected into the system prompt.
        max_tool_calls_per_turn  Cap before forcing REPLY (default 4).
        terminal_id         Logical terminal id for the session key (default "term-0").
                            Reserved for the multi-terminal extension.
        progress            Optional callable(fsm_name, scenario_name, details).
    """

    def __init__(
        self,
        *,
        llm_client: UserProxyLLMClient,
        controller: SandboxController,
        evaluation_run_id: str,
        observable_problem_statement: str,
        max_tool_calls_per_turn: int = 4,
        terminal_id: str = "term-0",
        progress: Callable[..., None] | None = None,
        scenario_name: str = "",
        turn: int | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.controller = controller
        self.evaluation_run_id = evaluation_run_id
        self.observable_problem_statement = observable_problem_statement
        self.max_tool_calls_per_turn = max_tool_calls_per_turn
        self.terminal_id = terminal_id
        self.progress = progress
        self.scenario_name = scenario_name
        self.turn = turn

        # Tool registry: {name: callable(ctx, tool_call) -> CommandExecutionResult}
        # Multi-terminal extension: add open_new_terminal here later.
        self.tool_registry: dict[str, Callable] = {
            "run_command": self._handle_run_command,
        }
        self.tools = DEFAULT_TOOLS

        # State action dispatch table (FSM pattern from model_router.py)
        self.state_actions: dict[UserProxyState, Callable] = {
            UserProxyState.READ_ASSISTANT: self._state_read_assistant,
            UserProxyState.DECIDE: self._state_decide,
            UserProxyState.TOOL_EXEC: self._state_tool_exec,
            UserProxyState.REPLY: self._state_reply,
            UserProxyState.DONE: self._state_done,
            UserProxyState.STALLED: self._state_stalled,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_turn(
        self,
        transcript: list[tuple[str, str]],
        subject_reply: str,
    ) -> UserProxyTurnResult:
        """Run one proxy turn (READ_ASSISTANT → ... → DONE | STALLED).

        Each call is self-contained: a fresh context and message history is
        built from the provided transcript so earlier turns are visible to the
        proxy LLM without maintaining server-side state.
        """
        system_msg = {
            "role": "system",
            "content": _user_proxy_system_prompt(self.observable_problem_statement),
        }
        ctx = UserProxyContext(
            transcript=list(transcript),
            assistant_reply=subject_reply,
            observable_problem_statement=self.observable_problem_statement,
            messages=[system_msg],
        )

        state = UserProxyState.READ_ASSISTANT
        while state not in (UserProxyState.DONE, UserProxyState.STALLED):
            handler = self.state_actions[state]
            next_state = handler(ctx)
            self._emit_progress(state, next_state, ctx)
            state = next_state

        return UserProxyTurnResult(
            user_message=ctx.final_reply,
            tool_results=tuple(ctx.tool_results),
            closure=ctx.closure,
            stalled=ctx.stalled,
        )

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _state_read_assistant(self, ctx: UserProxyContext) -> UserProxyState:
        """Build the initial user message from transcript + subject reply."""
        rendered = "\n".join(f"{role}: {content}" for role, content in ctx.transcript)
        if rendered:
            turn_text = f"Conversation so far:\n{rendered}\n\nAssistant just said:\n{ctx.assistant_reply}"
        else:
            turn_text = ctx.assistant_reply
        ctx.messages.append({"role": "user", "content": turn_text})
        return UserProxyState.DECIDE

    def _state_decide(self, ctx: UserProxyContext) -> UserProxyState:
        """Call the LLM and decide what to do next."""
        response: UserProxyLLMResponse = self.llm_client.chat(
            ctx.messages,
            tools=self.tools,
        )

        # Build the assistant message to append
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if response.content:
            assistant_msg["content"] = response.content
        else:
            assistant_msg["content"] = None

        if response.tool_calls:
            # OpenAI format: tool_calls array in the assistant message
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments
                        if isinstance(tc.arguments, str)
                        else __import__("json").dumps(tc.arguments),
                    },
                }
                for tc in response.tool_calls
            ]
            ctx.messages.append(assistant_msg)
            ctx._pending_tool_calls = list(response.tool_calls)

            if ctx.tool_call_count >= self.max_tool_calls_per_turn:
                # Cap exceeded — force reply with whatever content we have
                return UserProxyState.REPLY

            return UserProxyState.TOOL_EXEC

        # No tool calls
        ctx.messages.append(assistant_msg)
        ctx._pending_tool_calls = []

        if not (response.content or "").strip():
            if ctx.tool_call_count == 0:
                # Model produced nothing at all — stall immediately
                ctx.stalled = True
                return UserProxyState.STALLED
            # Had tool calls before but now empty content — treat as REPLY
            return UserProxyState.REPLY

        return UserProxyState.REPLY

    def _state_tool_exec(self, ctx: UserProxyContext) -> UserProxyState:
        """Dispatch pending tool calls and feed results back."""
        for tc in ctx._pending_tool_calls:
            handler = self.tool_registry.get(tc.name)
            if handler is None:
                error_content = f"Unknown tool: {tc.name}"
                ctx.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": error_content,
                    }
                )
                continue
            try:
                result = handler(ctx, tc)
                ctx.tool_results.append(result)
                ctx.tool_call_count += 1
                ctx.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _render_tool_result(result),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                error_content = f"Tool error: {exc}"
                ctx.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": error_content,
                    }
                )

        ctx._pending_tool_calls = []
        return UserProxyState.DECIDE

    def _state_reply(self, ctx: UserProxyContext) -> UserProxyState:
        """Extract final content and detect closure."""
        # Find the last assistant message with content
        final_content = ""
        for msg in reversed(ctx.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                final_content = str(msg["content"])
                break

        if not final_content.strip():
            ctx.stalled = True
            return UserProxyState.STALLED

        ctx.final_reply = final_content

        # Closure detection
        if final_content.strip() == "REPAIR_CONFIRMED":
            ctx.closure = True
        elif _looks_like_closure_reply(final_content):
            ctx.closure = True

        return UserProxyState.DONE

    def _state_done(self, ctx: UserProxyContext) -> UserProxyState:
        return UserProxyState.DONE

    def _state_stalled(self, ctx: UserProxyContext) -> UserProxyState:
        ctx.stalled = True
        return UserProxyState.STALLED

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    def _handle_run_command(
        self,
        ctx: UserProxyContext,
        tool_call: UserProxyToolCall,
    ) -> CommandExecutionResult:
        command = str(tool_call.arguments.get("command", "")).strip()
        if not command:
            raise ValueError("run_command called with empty command")
        results = self.controller.execute_commands(
            (command,),
            session_key=f"{self.evaluation_run_id}-proxy-{self.terminal_id}",
        )
        if not results:
            raise RuntimeError(f"execute_commands returned empty for command: {command!r}")
        return results[0]

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------

    def _emit_progress(
        self,
        from_state: UserProxyState,
        to_state: UserProxyState,
        ctx: UserProxyContext,
    ) -> None:
        if self.progress is None:
            return
        try:
            details: dict[str, Any] = {
                "from": from_state.name,
                "to": to_state.name,
                "tool_call_count": ctx.tool_call_count,
            }
            if self.turn is not None:
                details["turn"] = self.turn
            # Include the first pending tool name when transitioning into TOOL_EXEC
            if to_state == UserProxyState.TOOL_EXEC and ctx._pending_tool_calls:
                details["tool"] = ctx._pending_tool_calls[0].name
            self.progress(
                fsm_name="user-proxy",
                scenario_name=self.scenario_name,
                details=details,
            )
        except Exception:  # noqa: BLE001
            pass
