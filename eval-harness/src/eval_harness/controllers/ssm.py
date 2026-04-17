from __future__ import annotations

import base64
import shlex
import time
from dataclasses import dataclass
from typing import Any

from .base import InteractiveSession, SandboxController, SandboxControllerFactory
from ..backends.base import SandboxHandle
from ..models import CommandExecutionResult, utc_now_iso


class SsmInteractiveSession(InteractiveSession):
    """A persistent terminal session backed by tmux executed over SSM send_command."""

    def __init__(self, controller: SsmController, session_key: str) -> None:
        self.controller = controller
        self.session_key = session_key
        # Ensure the session is ready
        self.reset()

    def send_input(self, input_text: str) -> None:
        script = f"""
import subprocess, base64
input_bytes = base64.b64decode({repr(base64.b64encode(input_text.encode('utf-8')).decode('ascii'))})
# Send literal characters
subprocess.run(['tmux', 'send-keys', '-t', {repr(self.session_key)}, '-l', input_bytes.decode('utf-8')])
"""
        self.controller.execute_command(f"python3 -c {shlex.quote(script)}", session_key=f"{self.session_key}-send")

    def read_output(self, timeout_seconds: float = 5.0) -> str:
        # Wait briefly for output to settle
        time.sleep(timeout_seconds)
        res = self.controller.execute_command(
            f"tmux capture-pane -p -t {shlex.quote(self.session_key)}",
            session_key=f"{self.session_key}-read"
        )
        self.controller.execute_command(
            f"tmux clear-history -t {shlex.quote(self.session_key)}",
            session_key=f"{self.session_key}-clear"
        )
        return res.stdout

    def reset(self) -> None:
        self.close()
        self.controller.execute_command(
            f"tmux new-session -d -s {shlex.quote(self.session_key)} 'bash'",
            session_key=f"{self.session_key}-reset"
        )

    def close(self) -> None:
        self.controller.execute_command(
            f"tmux kill-session -t {shlex.quote(self.session_key)} || true",
            session_key=f"{self.session_key}-close"
        )


def _require_boto3() -> Any:
    try:
        import boto3  # type: ignore[import-untyped]
        return boto3
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "boto3 is required for the SSM controller. Install it with: pip install boto3"
        ) from exc


_SSM_TRANSIENT_STATUSES = {"Pending", "InProgress", "Delayed"}


@dataclass(frozen=True)
class SsmControllerConfig:
    instance_id: str
    default_session_key: str
    command_timeout_seconds: int = 600


@dataclass(frozen=True)
class SsmControllerFactoryConfig:
    default_session_key_prefix: str
    aws_region: str
    command_timeout_seconds: int = 600


class SsmController(SandboxController):
    """Executes commands on an EC2 instance via AWS SSM send_command."""

    name = "ssm"

    def __init__(self, config: SsmControllerConfig, *, ssm_client: Any | None = None) -> None:
        self.config = config
        if ssm_client is not None:
            self._ssm = ssm_client
        else:
            self._ssm = None  # lazily initialised on first use

    def _client(self) -> Any:
        if self._ssm is None:
            boto3 = _require_boto3()
            self._ssm = boto3.client("ssm")
        return self._ssm

    def _build_comment(self, session_key: str | None) -> str:
        key = session_key or self.config.default_session_key
        comment = f"eval-harness ssm-exec {key}"
        # AWS Comment field is limited to 100 characters.
        return comment[:100]

    def _poll_invocation(
        self,
        command_id: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        ssm = self._client()
        instance_id = self.config.instance_id
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                invocation = ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                )
            except Exception as exc:  # noqa: BLE001
                # boto3 raises ClientError; handle without importing botocore
                error_code = ""
                response = getattr(exc, "response", None)
                if response is not None:
                    error_code = str(response.get("Error", {}).get("Code", "")).strip()
                if error_code == "InvocationDoesNotExist":
                    time.sleep(2)
                    continue
                raise
            status = str(invocation.get("Status", "")).strip()
            if status in _SSM_TRANSIENT_STATUSES:
                time.sleep(3)
                continue
            return invocation
        raise TimeoutError(
            f"SSM command {command_id!r} on instance {instance_id!r} "
            f"did not complete within {timeout_seconds}s."
        )

    def execute_commands(
        self,
        commands: tuple[str, ...],
        *,
        agent_id: str = "",
        session_key: str | None = None,
    ) -> tuple[CommandExecutionResult, ...]:
        # agent_id is ignored — kept for interface compatibility (removed in Phase 5).
        ssm = self._client()
        instance_id = self.config.instance_id
        comment = self._build_comment(session_key)
        results: list[CommandExecutionResult] = []

        for cmd in commands:
            started_at = utc_now_iso()
            response = ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [cmd]},
                Comment=comment,
                CloudWatchOutputConfig={"CloudWatchOutputEnabled": False},
            )
            command_id = str(response["Command"]["CommandId"])

            invocation = self._poll_invocation(
                command_id,
                timeout_seconds=self.config.command_timeout_seconds,
            )

            status = str(invocation.get("Status", "")).strip()
            status_details = str(invocation.get("StatusDetails", "")).strip()
            response_code = invocation.get("ResponseCode")
            stdout = str(invocation.get("StandardOutputContent", ""))
            stderr = str(invocation.get("StandardErrorContent", ""))

            if response_code is None:
                # SSM failed to execute the script (e.g. TimedOut, Cancelled, Undeliverable).
                stderr = (
                    f"[SSM-EXEC-FAILURE status={status!r} status_details={status_details!r}] "
                    + stderr
                ).strip()
                exit_code: int | None = -1
            else:
                exit_code = int(response_code)

            finished_at = utc_now_iso()
            results.append(
                CommandExecutionResult(
                    command=cmd,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    started_at=started_at,
                    finished_at=finished_at,
                    metadata={
                        "command_id": command_id,
                        "ssm_status": status,
                        "ssm_status_details": status_details,
                    },
                )
            )

        return tuple(results)

    def open_session(self, session_key: str) -> InteractiveSession:
        return SsmInteractiveSession(self, session_key)

    def close(self) -> None:
        # SSM is stateless per call — nothing to tear down.
        pass


class SsmControllerFactory(SandboxControllerFactory):
    def __init__(
        self,
        config: SsmControllerFactoryConfig,
        *,
        ssm_client: Any | None = None,
    ) -> None:
        self.config = config
        self._ssm_client = ssm_client

    def open(self, handle: SandboxHandle, *, purpose: str = "") -> SandboxController:
        prefix = self.config.default_session_key_prefix
        session_key = f"{prefix}-{purpose}" if purpose else prefix
        controller_config = SsmControllerConfig(
            instance_id=handle.remote_id,
            default_session_key=session_key,
            command_timeout_seconds=self.config.command_timeout_seconds,
        )
        return SsmController(controller_config, ssm_client=self._ssm_client)
