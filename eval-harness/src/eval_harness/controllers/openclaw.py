from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

from .base import SandboxController, SandboxControllerFactory
from ..backends.base import SandboxHandle
from ..models import CommandExecutionResult, utc_now_iso
from ..runtime.ssm import SsmPortForwardSession

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None


def _require_requests() -> None:
    if requests is None:  # pragma: no cover - depends on optional dependency
        raise RuntimeError("requests is required for the OpenClaw controller.")


_STDOUT_BEGIN = "__STDOUT_BEGIN__"
_STDOUT_END = "__STDOUT_END__"
_STDERR_BEGIN = "__STDERR_BEGIN__"
_STDERR_END = "__STDERR_END__"
_EXIT_CODE_PREFIX = "__EXIT_CODE__="
_REQUIRED_RESULT_MARKERS = (_STDOUT_BEGIN, _STDOUT_END, _STDERR_BEGIN, _STDERR_END)


@dataclass(frozen=True)
class OpenClawControllerConfig:
    base_url: str
    token: str
    default_session_key: str
    request_timeout_seconds: int = 900


@dataclass(frozen=True)
class OpenClawControllerFactoryConfig:
    token: str
    default_session_key_prefix: str
    request_timeout_seconds: int = 900
    fixed_base_url: str | None = None
    aws_region: str | None = None
    remote_port: int = 18789


def _allocate_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class OpenClawController(SandboxController):
    name = "openclaw"

    def __init__(self, config: OpenClawControllerConfig, port_forward_session: Any | None = None):
        _require_requests()
        self.config = config
        self.port_forward_session = port_forward_session
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {config.token}",
                "Content-Type": "application/json",
            }
        )

    def _extract_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
            return "".join(text_parts)
        return str(content)

    def _command_prompt(self, command: str) -> str:
        return (
            "Run the exact shell command below on the target machine and return only the structured result.\n"
            "Use the normal host execution path. Do not use exec host=sandbox.\n"
            "If you need to choose an execution host, use host=gateway.\n"
            f"Format exactly as:\n{_STDOUT_BEGIN}\n<stdout>\n{_STDOUT_END}\n"
            f"{_STDERR_BEGIN}\n<stderr>\n{_STDERR_END}\n"
            f"{_EXIT_CODE_PREFIX}<integer>\n\n"
            "Do not explain anything. Do not omit empty sections.\n\n"
            f"Command:\n{command}"
        )

    def _extract_block(self, text: str, start_marker: str, end_marker: str) -> str:
        start_index = text.find(start_marker)
        end_index = text.find(end_marker)
        if start_index == -1 or end_index == -1 or end_index < start_index:
            return ""
        start_index += len(start_marker)
        return text[start_index:end_index].strip("\n")

    def _parse_command_result(self, command: str, output: str) -> CommandExecutionResult:
        stdout = self._extract_block(output, _STDOUT_BEGIN, _STDOUT_END)
        stderr = self._extract_block(output, _STDERR_BEGIN, _STDERR_END)
        exit_code: int | None = None
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith(_EXIT_CODE_PREFIX):
                raw_value = stripped[len(_EXIT_CODE_PREFIX):].strip()
                try:
                    exit_code = int(raw_value)
                except ValueError:
                    exit_code = None
                break
        return CommandExecutionResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            metadata={"raw_output": output},
        )

    def _looks_like_sandbox_refusal(self, output: str) -> bool:
        normalized = str(output or "").lower()
        if "sandbox" not in normalized:
            return False
        return any(
            marker in normalized
            for marker in (
                "permission",
                "approval",
                "/approve",
                "cannot",
                "can't",
                "cant",
                "refus",
                "host=sandbox",
            )
        )

    def _require_structured_command_result(self, command: str, output: str, parsed: CommandExecutionResult) -> CommandExecutionResult:
        missing_markers = [marker for marker in _REQUIRED_RESULT_MARKERS if marker not in output]
        if not missing_markers and parsed.exit_code is not None:
            return parsed

        raw_preview = " ".join(str(output or "").split())
        if len(raw_preview) > 400:
            raw_preview = raw_preview[:397] + "..."
        if self._looks_like_sandbox_refusal(output):
            reason = "OpenClaw refused host command execution and mentioned sandbox restrictions."
        else:
            reason = "OpenClaw returned an unstructured command result."
        raise RuntimeError(
            f"{reason} command={command!r} "
            f"missing_markers={missing_markers or ['none']} "
            f"exit_code_present={parsed.exit_code is not None} "
            f"raw_output={raw_preview!r}"
        )

    def send(self, *, agent_id: str, message: str, session_key: str | None = None) -> str:
        payload = {
            "model": f"openclaw/{agent_id}",
            "user": session_key or self.config.default_session_key,
            "messages": [{"role": "user", "content": message}],
        }
        try:
            response = self.session.post(
                f"{self.config.base_url.rstrip('/')}/v1/chat/completions",
                json=payload,
                timeout=self.config.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                "OpenClaw request failed "
                f"(base_url={self.config.base_url!r}, agent_id={agent_id!r}, session_key={(session_key or self.config.default_session_key)!r}): {exc}"
            ) from exc
        response.raise_for_status()
        return self._extract_text(response.json())

    def execute_commands(
        self,
        commands: tuple[str, ...],
        *,
        agent_id: str,
        session_key: str | None = None,
    ) -> tuple[CommandExecutionResult, ...]:
        results: list[CommandExecutionResult] = []
        for command in commands:
            started_at = utc_now_iso()
            raw_output = self.send(
                agent_id=agent_id,
                message=self._command_prompt(command),
                session_key=session_key,
            )
            parsed = self._require_structured_command_result(
                command,
                raw_output,
                self._parse_command_result(command, raw_output),
            )
            results.append(
                CommandExecutionResult(
                    command=parsed.command,
                    stdout=parsed.stdout,
                    stderr=parsed.stderr,
                    exit_code=parsed.exit_code,
                    started_at=started_at,
                    finished_at=utc_now_iso(),
                    metadata=parsed.metadata,
                )
            )
        return tuple(results)

    def close(self) -> None:
        self.session.close()
        if self.port_forward_session is not None:
            self.port_forward_session.stop()


class OpenClawControllerFactory(SandboxControllerFactory):
    def __init__(self, config: OpenClawControllerFactoryConfig):
        self.config = config

    def open(self, handle: SandboxHandle, *, purpose: str = "") -> SandboxController:
        session_key = self.config.default_session_key_prefix
        if purpose:
            session_key = f"{session_key}-{purpose}"
        if self.config.fixed_base_url:
            controller_config = OpenClawControllerConfig(
                base_url=self.config.fixed_base_url,
                token=self.config.token,
                default_session_key=session_key,
                request_timeout_seconds=self.config.request_timeout_seconds,
            )
            return OpenClawController(controller_config)

        if not self.config.aws_region:
            raise RuntimeError("aws_region is required when fixed_base_url is not configured.")

        last_error: Exception | None = None
        for _ in range(5):
            local_port = _allocate_local_port()
            port_forward = SsmPortForwardSession(
                instance_id=handle.remote_id,
                local_port=local_port,
                remote_port=self.config.remote_port,
                region=self.config.aws_region,
            )
            try:
                port_forward.start()
            except Exception as exc:  # noqa: BLE001 - retry on any bind/port-collision failure
                last_error = exc
                continue
            controller_config = OpenClawControllerConfig(
                base_url=f"http://127.0.0.1:{local_port}",
                token=self.config.token,
                default_session_key=session_key,
                request_timeout_seconds=self.config.request_timeout_seconds,
            )
            return OpenClawController(controller_config, port_forward_session=port_forward)
        raise RuntimeError(
            f"Failed to start SSM port forward to {handle.remote_id} after 5 attempts: {last_error}"
        )
