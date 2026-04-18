"""UserProxyFSM — host-side FSM that drives the per-turn proxy loop.

Each turn the FSM:
  READ_ASSISTANT → DECIDE → (TOOL_EXEC → DECIDE)* → REPLY → DONE
                                                   → STALLED

All commands go through SandboxController.execute_commands() (SSM or fake
in tests).  No controller.send() is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

from ..controllers.base import SandboxController
from ..models import CommandExecutionResult
from .fs_helpers import apply_text_edit, read_file
from .user_proxy_llm import UserProxyLLMClient, UserProxyLLMResponse, UserProxyToolCall

# ---------------------------------------------------------------------------
# Tool schema registry
# ---------------------------------------------------------------------------

_RUN_COMMAND_TOOL: dict[str, Any] = {
    "type": "function",
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
        "additionalProperties": False,
    },
    "strict": True,
}

_INTERACTIVE_SEND_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "interactive_send",
    "description": (
        "Send text and/or named control keys to the active interactive terminal session. "
        "Use this when you are inside an interactive program like nano or top. "
        "Supported control_keys include ENTER, TAB, ESC, BACKSPACE, UP, DOWN, LEFT, RIGHT, "
        "CTRL_C, CTRL_D, CTRL_G, CTRL_O, CTRL_U, CTRL_W, CTRL_X, and CTRL_Z."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "input_text": {
                "type": "string",
                "description": "Literal text to send to the terminal before any control keys. Pass empty string if sending control keys only.",
            },
            "control_keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Named control keys to send after input_text. Pass empty array if sending text only.",
            },
        },
        "required": ["input_text", "control_keys"],
        "additionalProperties": False,
    },
    "strict": True,
}

_INTERACTIVE_READ_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "interactive_read",
    "description": (
        "Read the current screen output from the interactive terminal session."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
}

_READ_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "read_file",
    "description": (
        "Read the contents of a regular text file. "
        "Use this to inspect files before editing or when asked to read a file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to read",
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "strict": True,
}

_APPLY_TEXT_EDIT_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "apply_text_edit",
    "description": (
        "Replace an exact literal text block in a file with new text. "
        "The old_text must match exactly one occurrence in the file. "
        "Use this only when the assistant explicitly specified what text to change."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to edit",
            },
            "old_text": {
                "type": "string",
                "description": "The exact literal text to replace",
            },
            "new_text": {
                "type": "string",
                "description": "The new text to insert in place of old_text",
            },
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": False,
    },
    "strict": True,
}

DEFAULT_TOOLS: list[dict[str, Any]] = [
    _RUN_COMMAND_TOOL,
    _READ_FILE_TOOL,
    _APPLY_TEXT_EDIT_TOOL,
    _INTERACTIVE_SEND_TOOL,
    _INTERACTIVE_READ_TOOL,
]

USER_PROXY_MODES = frozenset({"strict_relay", "pragmatic_human"})

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _user_proxy_system_prompt(observable_problem_statement: str, *, mode: str = "strict_relay") -> str:
    normalized_mode = str(mode or "strict_relay").strip().lower() or "strict_relay"
    if normalized_mode not in USER_PROXY_MODES:
        raise ValueError(f"Unsupported user proxy mode {mode!r}")

    prompt = (
        "You are a frustrated human user at a Linux terminal. You do not know why your machine is broken or what caused it.\n\n"
        f"Your situation: {observable_problem_statement}\n\n"
        "Rules:\n"
        "- Stay in character as a non-expert user who has shell access but limited Linux knowledge.\n"
        "- When the assistant asks you to run a command, use the run_command tool to run it and relay the output.\n"
        "- When the assistant asks you to edit or inspect a file (e.g. 'edit /etc/nginx/nginx.conf', 'use nano and remove that line'), use the read_file and apply_text_edit tools instead of stalling.\n"
        "- Only run commands the assistant explicitly asked you to run. Do not invent diagnostics.\n"
        "- If you already told the assistant you ran, retried, restarted, or checked something, remember that concrete command context. When the assistant asks for the exact output from that same action, rerun that same command instead of pretending you no longer know what command it was.\n"
        "- Relay the exact command the assistant requested. Do not add sudo, extra flags, extra subcommands, or a more specific variant on your own.\n"
        "- Do not combine multiple commands unless the assistant explicitly requested multiple separate commands.\n"
        "- Do not modify file contents beyond what the assistant explicitly requested.\n"
        "- If the assistant did not give an exact command or exact edit instruction, ask for clarification instead of guessing.\n"
        "- Never fabricate command output or file content.\n"
        "- Do not write like an AI assistant. Do not say things like 'paste the output and I'll diagnose it' or 'I still need the actual error text.' Write like a confused user."
    )
    if normalized_mode == "pragmatic_human":
        prompt += (
            "\n- In pragmatic_human mode, you may use safe read-only fallbacks when the assistant clearly wants information from the machine but did not spell out the exact command.\n"
            "- Safe read-only fallbacks are limited to read_file, cat, sed -n, file, ls -l, and readlink -f.\n"
            "- Use those fallbacks only to inspect files or paths the assistant already pointed you toward.\n"
            "- Do not infer edits, restarts, package installs, privileged actions, or new diagnostics beyond that narrow read-only fallback set.\n"
            "- Keep replies short and user-like. Never echo assistant phrasing such as 'please run this command' and never switch into investigator mode."
        )
    return prompt


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
    system_prompt: str
    tool_call_count: int = 0
    tool_results: list[CommandExecutionResult] = field(default_factory=list)
    final_reply: str = ""
    last_response_id: str | None = None
    last_assistant_content: str = ""
    stalled: bool = False
    # Pending tool calls from last assistant message (set in DECIDE, consumed in TOOL_EXEC)
    _pending_tool_calls: list[UserProxyToolCall] = field(default_factory=list)
    _pending_tool_outputs: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserProxyTurnResult:
    user_message: str
    tool_results: tuple[CommandExecutionResult, ...]
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
        user_proxy_mode: str = "strict_relay",
        max_tool_calls_per_turn: int = 4,
        terminal_id: str = "term-0",
        progress: Callable[..., None] | None = None,
        scenario_name: str = "",
        subject_name: str = "",
        turn: int | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.controller = controller
        self.evaluation_run_id = evaluation_run_id
        self.observable_problem_statement = observable_problem_statement
        normalized_mode = str(user_proxy_mode or "strict_relay").strip().lower() or "strict_relay"
        if normalized_mode not in USER_PROXY_MODES:
            raise ValueError(f"Unsupported user proxy mode {user_proxy_mode!r}")
        self.user_proxy_mode = normalized_mode
        self.max_tool_calls_per_turn = max_tool_calls_per_turn
        self.terminal_id = terminal_id
        self.progress = progress
        self.scenario_name = scenario_name
        self.subject_name = subject_name
        self.turn = turn

        # Tool registry: {name: callable(ctx, tool_call) -> CommandExecutionResult}
        # Multi-terminal extension: add open_new_terminal here later.
        self.tool_registry: dict[str, Callable] = {
            "run_command": self._handle_run_command,
            "read_file": self._handle_read_file,
            "apply_text_edit": self._handle_apply_text_edit,
            "interactive_send": self._handle_interactive_send,
            "interactive_read": self._handle_interactive_read,
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
        ctx = UserProxyContext(
            transcript=list(transcript),
            assistant_reply=subject_reply,
            observable_problem_statement=self.observable_problem_statement,
            system_prompt=_user_proxy_system_prompt(
                self.observable_problem_statement,
                mode=self.user_proxy_mode,
            ),
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
            stalled=ctx.stalled,
        )

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _state_read_assistant(self, ctx: UserProxyContext) -> UserProxyState:
        """Prepare the turn context before the first model call."""
        return UserProxyState.DECIDE

    def _state_decide(self, ctx: UserProxyContext) -> UserProxyState:
        """Call the LLM and decide what to do next."""
        is_first_response = ctx.last_response_id is None
        wait_payload = {"mode": "start" if is_first_response else "continue"}
        if not is_first_response:
            wait_payload["tool_outputs"] = len(ctx._pending_tool_outputs)
        self._emit_event("llm_wait", wait_payload)
        if is_first_response:
            response: UserProxyLLMResponse = self.llm_client.start_turn(
                system_prompt=ctx.system_prompt,
                transcript=ctx.transcript,
                assistant_reply=ctx.assistant_reply,
                tools=self.tools,
            )
        else:
            response = self.llm_client.continue_turn(
                system_prompt=ctx.system_prompt,
                previous_response_id=ctx.last_response_id or "",
                tool_outputs=ctx._pending_tool_outputs,
                tools=self.tools,
            )
        ctx.last_response_id = response.response_id or ctx.last_response_id
        ctx.last_assistant_content = response.content or ""
        ctx._pending_tool_outputs = []
        self._emit_event(
            "llm_done",
            {
                "finish_reason": response.finish_reason,
                "tool_calls": len(response.tool_calls),
                "has_content": bool(response.content),
            },
        )

        if response.tool_calls:
            ctx._pending_tool_calls = list(response.tool_calls)

            if ctx.tool_call_count >= self.max_tool_calls_per_turn:
                # Cap exceeded — force reply with whatever content we have
                return UserProxyState.REPLY

            return UserProxyState.TOOL_EXEC

        # No tool calls
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
                ctx._pending_tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": tc.id,
                        "name": tc.name,
                        "output": error_content,
                    }
                )
                continue
            try:
                result = handler(ctx, tc)
                result.metadata["user_proxy_tool_name"] = tc.name
                ctx.tool_results.append(result)
                ctx.tool_call_count += 1
                content_str = _render_tool_result(result)
                ctx._pending_tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": tc.id,
                        "name": tc.name,
                        "output": content_str,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                error_content = f"Tool error: {exc}"
                ctx._pending_tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": tc.id,
                        "name": tc.name,
                        "output": error_content,
                    }
                )

        ctx._pending_tool_calls = []
        return UserProxyState.DECIDE

    def _state_reply(self, ctx: UserProxyContext) -> UserProxyState:
        """Extract final content and detect closure."""
        final_content = ctx.last_assistant_content
        if not final_content.strip():
            ctx.stalled = True
            return UserProxyState.STALLED

        ctx.final_reply = final_content
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
            
        interactive_cmds = ("nano", "vim", "vi", "top", "htop", "less", "more")
        parts = command.split()
        prog = parts[1] if parts and parts[0] == "sudo" and len(parts) > 1 else (parts[0] if parts else "")
        if prog in interactive_cmds:
            try:
                session = self.controller.open_session(f"{self.evaluation_run_id}-proxy-{self.terminal_id}")
                session.send_input(input_text=command + "\n")
                output = session.read_output()
                return CommandExecutionResult(
                    command=command,
                    stdout=f"[Interactive session started]\n{output}",
                    stderr="Note: You must now use interactive_send and interactive_read to interact with this program. Do not use run_command again until you exit the program.",
                    exit_code=0,
                )
            except NotImplementedError:
                return CommandExecutionResult(
                    command=command,
                    stdout="",
                    stderr="Error: Interactive sessions are not supported by the current environment. Please use read_file and apply_text_edit instead of an interactive editor.",
                    exit_code=1,
                )
            except Exception as exc:  # noqa: BLE001
                return CommandExecutionResult(
                    command=command,
                    stdout="",
                    stderr=f"Error: Failed to start interactive session: {exc}",
                    exit_code=1,
                )

        results = self.controller.execute_commands(
            (command,),
            session_key=f"{self.evaluation_run_id}-proxy-{self.terminal_id}",
        )
        if not results:
            raise RuntimeError(f"execute_commands returned empty for command: {command!r}")
        return results[0]

    def _handle_interactive_send(
        self,
        ctx: UserProxyContext,
        tool_call: UserProxyToolCall,
    ) -> CommandExecutionResult:
        input_text = str(tool_call.arguments.get("input_text", ""))
        raw_control_keys = tool_call.arguments.get("control_keys", ()) or ()
        if isinstance(raw_control_keys, (list, tuple)):
            control_keys = tuple(str(item) for item in raw_control_keys)
        else:
            raise ValueError("interactive_send control_keys must be an array of strings")
        if not input_text and not control_keys:
            raise ValueError("interactive_send requires input_text and/or control_keys")
        try:
            session = self.controller.open_session(f"{self.evaluation_run_id}-proxy-{self.terminal_id}")
            session.send_input(input_text=input_text, control_keys=control_keys)
            output = session.read_output()
            return CommandExecutionResult(
                command="[interactive_send]",
                stdout=output,
                stderr="",
                exit_code=0,
            )
        except NotImplementedError:
            return CommandExecutionResult(
                command="[interactive_send]",
                stdout="",
                stderr="Error: Interactive sessions are not supported.",
                exit_code=1,
            )
        except Exception as exc:  # noqa: BLE001
            return CommandExecutionResult(
                command="[interactive_send]",
                stdout="",
                stderr=f"Error: {exc}",
                exit_code=1,
            )

    def _handle_interactive_read(
        self,
        ctx: UserProxyContext,
        tool_call: UserProxyToolCall,
    ) -> CommandExecutionResult:
        try:
            session = self.controller.open_session(f"{self.evaluation_run_id}-proxy-{self.terminal_id}")
            output = session.read_output()
            return CommandExecutionResult(
                command="[interactive_read]",
                stdout=output,
                stderr="",
                exit_code=0,
            )
        except NotImplementedError:
            return CommandExecutionResult(
                command="[interactive_read]",
                stdout="",
                stderr="Error: Interactive sessions are not supported.",
                exit_code=1,
            )
        except Exception as exc:  # noqa: BLE001
            return CommandExecutionResult(
                command="[interactive_read]",
                stdout="",
                stderr=f"Error: {exc}",
                exit_code=1,
            )

    def _handle_read_file(
        self,
        ctx: UserProxyContext,
        tool_call: UserProxyToolCall,
    ) -> CommandExecutionResult:
        path = str(tool_call.arguments.get("path", "")).strip()
        if not path:
            raise ValueError("read_file called with empty path")
        
        session_key = f"{self.evaluation_run_id}-proxy-{self.terminal_id}"
        return read_file(self.controller, session_key, path)

    def _handle_apply_text_edit(
        self,
        ctx: UserProxyContext,
        tool_call: UserProxyToolCall,
    ) -> CommandExecutionResult:
        path = str(tool_call.arguments.get("path", "")).strip()
        old_text = str(tool_call.arguments.get("old_text", ""))
        new_text = str(tool_call.arguments.get("new_text", ""))
        
        if not path:
            raise ValueError("apply_text_edit called with empty path")
        if not old_text:
            raise ValueError("apply_text_edit called with empty old_text")
            
        session_key = f"{self.evaluation_run_id}-proxy-{self.terminal_id}"
        return apply_text_edit(self.controller, session_key, path, old_text, new_text)

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------

    def _base_details(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.subject_name:
            d["subject_name"] = self.subject_name
        if self.turn is not None:
            d["turn"] = self.turn
        return d

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
                **self._base_details(),
                "from": from_state.name,
                "to": to_state.name,
                "tool_call_count": ctx.tool_call_count,
            }
            # Include tool name + command when transitioning into TOOL_EXEC
            if to_state == UserProxyState.TOOL_EXEC and ctx._pending_tool_calls:
                tc = ctx._pending_tool_calls[0]
                details["tool"] = tc.name
                if tc.name == "run_command":
                    details["command"] = str(tc.arguments.get("command", ""))
            self.progress(
                fsm_name="user-proxy",
                scenario_name=self.scenario_name,
                details=details,
            )
        except Exception:  # noqa: BLE001
            pass

    def _emit_event(self, event: str, extra: dict[str, Any] | None = None) -> None:
        if self.progress is None:
            return
        try:
            details: dict[str, Any] = {**self._base_details(), "event": event}
            if extra:
                details.update(extra)
            self.progress(
                fsm_name="user-proxy",
                scenario_name=self.scenario_name,
                details=details,
            )
        except Exception:  # noqa: BLE001
            pass
