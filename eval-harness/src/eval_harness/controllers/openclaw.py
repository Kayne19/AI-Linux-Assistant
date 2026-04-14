from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import SandboxController
from ..models import VerificationCheck, VerificationMatchMode, VerificationResult, utc_now_iso

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None


def _require_requests() -> None:
    if requests is None:  # pragma: no cover - depends on optional dependency
        raise RuntimeError("requests is required for the OpenClaw controller.")


@dataclass(frozen=True)
class OpenClawControllerConfig:
    base_url: str
    token: str
    default_session_key: str
    request_timeout_seconds: int = 60


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

    def _parse_exit_code(self, output: str) -> tuple[str, int | None]:
        marker = "__EXIT_CODE__="
        lines = output.splitlines()
        for index in range(len(lines) - 1, -1, -1):
            line = lines[index].strip()
            if line.startswith(marker):
                raw_value = line[len(marker):].strip()
                try:
                    exit_code = int(raw_value)
                except ValueError:
                    return output, None
                trimmed = "\n".join(lines[:index]).rstrip()
                return trimmed, exit_code
        return output, None

    def send(self, *, agent_id: str, message: str, session_key: str | None = None) -> str:
        payload = {
            "model": f"openclaw/{agent_id}",
            "user": session_key or self.config.default_session_key,
            "messages": [{"role": "user", "content": message}],
        }
        response = self.session.post(
            f"{self.config.base_url.rstrip('/')}/v1/chat/completions",
            json=payload,
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        return self._extract_text(response.json())

    def run_verification(self, check: VerificationCheck, *, agent_id: str, session_key: str | None = None) -> VerificationResult:
        started_at = utc_now_iso()
        verify_prompt = (
            "Run this exact command in the sandbox. Return the raw stdout/stderr only. "
            "After the raw output, append one final line exactly like __EXIT_CODE__=<integer>. "
            f"Do not explain anything.\n\n{check.command}"
        )
        raw_output = self.send(agent_id=agent_id, message=verify_prompt, session_key=session_key)
        output, actual_exit_code = self._parse_exit_code(raw_output)
        success = True
        expected = list(check.expected_substrings)
        if expected:
            if check.match_mode == VerificationMatchMode.ALL:
                success = all(item in output for item in expected)
            else:
                success = any(item in output for item in expected)
        if check.expected_exit_code is not None:
            success = success and actual_exit_code == check.expected_exit_code
        finished_at = utc_now_iso()
        return VerificationResult(
            check_name=check.name,
            command=check.command,
            success=success,
            output=output,
            expected_exit_code=check.expected_exit_code,
            actual_exit_code=actual_exit_code,
            started_at=started_at,
            finished_at=finished_at,
        )

    def close(self) -> None:
        self.session.close()
        if self.port_forward_session is not None:
            self.port_forward_session.stop()
