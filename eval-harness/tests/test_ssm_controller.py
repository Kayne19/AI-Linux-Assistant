from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ── path bootstrap (mirrors other test files in this suite) ──────────────────
SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    from types import ModuleType
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.controllers.ssm import (
    SsmController,
    SsmControllerConfig,
    SsmControllerFactory,
    SsmControllerFactoryConfig,
)
from eval_harness.backends.base import SandboxHandle


# ── Fake SSM client ──────────────────────────────────────────────────────────

@dataclass
class FakeSsmClient:
    """Hand-rolled fake SSM client backed by scripted response queues."""

    # Queue of (CommandId, invocation_responses) pairs.
    # Each send_command call pops one entry; invocation_responses is a deque
    # of get_command_invocation return values (polled in order).
    send_command_queue: deque = field(default_factory=deque)
    send_command_calls: list[dict[str, Any]] = field(default_factory=list)
    get_invocation_calls: list[dict[str, Any]] = field(default_factory=list)

    def send_command(self, **kwargs: Any) -> dict[str, Any]:
        self.send_command_calls.append(kwargs)
        command_id, _invocations = self.send_command_queue[0]  # peek, don't pop
        # pop happens in get_command_invocation when responses are exhausted
        return {"Command": {"CommandId": command_id}}

    def get_command_invocation(self, **kwargs: Any) -> dict[str, Any]:
        self.get_invocation_calls.append(kwargs)
        command_id, invocations = self.send_command_queue[0]
        response = invocations.popleft()
        if not invocations:
            self.send_command_queue.popleft()  # exhausted — advance to next command slot
        if isinstance(response, Exception):
            raise response
        return response

    def _enqueue(self, command_id: str, responses: list[dict[str, Any]]) -> None:
        self.send_command_queue.append((command_id, deque(responses)))


def _success_invocation(stdout: str = "hello\n", stderr: str = "", exit_code: int = 0) -> dict[str, Any]:
    return {
        "Status": "Success",
        "StatusDetails": "Success",
        "ResponseCode": exit_code,
        "StandardOutputContent": stdout,
        "StandardErrorContent": stderr,
    }


def _make_controller(
    fake_client: FakeSsmClient,
    instance_id: str = "i-abc123",
    session_key: str = "test-session",
    timeout: int = 30,
) -> SsmController:
    config = SsmControllerConfig(
        instance_id=instance_id,
        default_session_key=session_key,
        command_timeout_seconds=timeout,
    )
    return SsmController(config, ssm_client=fake_client)


# ── Test: single command success ─────────────────────────────────────────────

def test_single_command_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-001", [_success_invocation(stdout="hello\n", stderr="", exit_code=0)])

    ctrl = _make_controller(fake)
    (result,) = ctrl.execute_commands(("echo hello",))

    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.command == "echo hello"
    assert result.metadata["command_id"] == "cmd-001"
    assert result.metadata["ssm_status"] == "Success"


# ── Test: multiple commands → separate send_command calls ────────────────────

def test_multiple_commands_separate_send_command_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-001", [_success_invocation(stdout="out1\n")])
    fake._enqueue("cmd-002", [_success_invocation(stdout="out2\n")])

    ctrl = _make_controller(fake)
    results = ctrl.execute_commands(("cmd1", "cmd2"))

    assert len(results) == 2
    assert len(fake.send_command_calls) == 2
    assert results[0].stdout == "out1\n"
    assert results[1].stdout == "out2\n"


# ── Test: transient InvocationDoesNotExist is retried ───────────────────────

class _FakeClientError(Exception):
    """Minimal stand-in for botocore.exceptions.ClientError."""
    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


def test_transient_invocation_does_not_exist_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    # First get_command_invocation raises InvocationDoesNotExist, second succeeds.
    fake._enqueue("cmd-001", [
        _FakeClientError("InvocationDoesNotExist"),
        _success_invocation(stdout="retry-ok\n"),
    ])

    ctrl = _make_controller(fake)
    (result,) = ctrl.execute_commands(("echo retry",))

    assert result.stdout == "retry-ok\n"
    assert result.exit_code == 0
    assert len(fake.get_invocation_calls) == 2


# ── Test: non-zero exit code preserved ───────────────────────────────────────

def test_nonzero_exit_code_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-001", [_success_invocation(stdout="", stderr="permission denied\n", exit_code=1)])

    ctrl = _make_controller(fake)
    (result,) = ctrl.execute_commands(("false",))

    assert result.exit_code == 1
    assert result.stderr == "permission denied\n"


# ── Test: SSM-level failure → exit_code=-1 and descriptive stderr ────────────

def test_ssm_level_failure_produces_exit_code_minus_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-001", [{
        "Status": "Failed",
        "StatusDetails": "NonZeroExitCode",
        "ResponseCode": None,
        "StandardOutputContent": "",
        "StandardErrorContent": "agent crashed",
    }])

    ctrl = _make_controller(fake)
    (result,) = ctrl.execute_commands(("bad-cmd",))

    assert result.exit_code == -1
    assert "SSM-EXEC-FAILURE" in result.stderr
    assert "Failed" in result.stderr


# ── Test: InProgress then Success — poller waits ─────────────────────────────

def test_poller_waits_through_in_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-001", [
        {"Status": "InProgress", "StatusDetails": "Running", "ResponseCode": None,
         "StandardOutputContent": "", "StandardErrorContent": ""},
        _success_invocation(stdout="done\n"),
    ])

    ctrl = _make_controller(fake)
    (result,) = ctrl.execute_commands(("long-cmd",))

    assert result.exit_code == 0
    assert result.stdout == "done\n"
    # Two get_command_invocation calls: one InProgress, one Success
    assert len(fake.get_invocation_calls) == 2


# ── Test: Comment field ≤ 100 chars and contains session_key ─────────────────

def test_comment_field_includes_session_key_and_is_at_most_100_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-001", [_success_invocation()])

    long_key = "a-very-long-session-key-" + "x" * 200
    ctrl = _make_controller(fake, session_key="default-key")
    ctrl.execute_commands(("echo hi",), session_key=long_key)

    call = fake.send_command_calls[0]
    comment = call["Comment"]
    assert len(comment) <= 100
    # The key should be present (or at least truncated, so the prefix appears)
    assert "a-very-long-session-key-" in comment or len(comment) == 100


def test_comment_field_uses_default_session_key_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-001", [_success_invocation()])

    ctrl = _make_controller(fake, session_key="my-default-key")
    ctrl.execute_commands(("echo hi",))  # no session_key kwarg

    call = fake.send_command_calls[0]
    assert "my-default-key" in call["Comment"]


# ── Test: factory.open produces controller bound to handle.remote_id ──────────

def test_factory_open_binds_to_handle_remote_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-001", [_success_invocation(stdout="factoryout\n")])

    factory_config = SsmControllerFactoryConfig(
        default_session_key_prefix="harness",
        aws_region="us-east-1",
        command_timeout_seconds=30,
    )
    factory = SsmControllerFactory(factory_config, ssm_client=fake)

    handle = SandboxHandle(handle_id="h-1", kind="ec2", remote_id="i-factory999", backend_name="aws")
    ctrl = factory.open(handle, purpose="setup-xyz")

    assert isinstance(ctrl, SsmController)
    assert ctrl.config.instance_id == "i-factory999"
    assert "harness" in ctrl.config.default_session_key
    assert "setup-xyz" in ctrl.config.default_session_key

    (result,) = ctrl.execute_commands(("echo factory",))
    assert result.stdout == "factoryout\n"

    # Verify the send_command targeted the correct instance
    assert fake.send_command_calls[0]["InstanceIds"] == ["i-factory999"]


def test_factory_open_without_purpose_uses_prefix_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    factory_config = SsmControllerFactoryConfig(
        default_session_key_prefix="eval-harness",
        aws_region="us-west-2",
    )
    factory = SsmControllerFactory(factory_config, ssm_client=FakeSsmClient())
    handle = SandboxHandle(handle_id="h-2", kind="ec2", remote_id="i-xyz", backend_name="aws")
    ctrl = factory.open(handle)
    assert ctrl.config.default_session_key == "eval-harness"


# ── Test: close is a no-op ────────────────────────────────────────────────────

def test_close_is_noop() -> None:
    config = SsmControllerConfig(
        instance_id="i-noop",
        default_session_key="key",
    )
    ctrl = SsmController(config, ssm_client=FakeSsmClient())
    ctrl.close()  # must not raise


# ── Test: agent_id kwarg is ignored (interface compatibility) ─────────────────

def test_agent_id_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-001", [_success_invocation(stdout="ok\n")])

    ctrl = _make_controller(fake)
    (result,) = ctrl.execute_commands(("echo ok",), agent_id="setup", session_key="s1")
    assert result.exit_code == 0

# ── Test: InteractiveSession ──────────────────────────────────────────────────

def test_interactive_session_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    
    # open_session() does a reset() which calls close() then new-session
    fake._enqueue("cmd-close-1", [_success_invocation()])
    fake._enqueue("cmd-new-1", [_success_invocation()])
    
    # send_input
    fake._enqueue("cmd-send", [_success_invocation()])
    
    # read_output
    fake._enqueue("cmd-read", [_success_invocation(stdout="test_output\n")])
    fake._enqueue("cmd-clear", [_success_invocation()])
    
    ctrl = _make_controller(fake)
    session = ctrl.open_session("test-interactive")
    
    session.send_input("echo hi")
    output = session.read_output(timeout_seconds=0)
    
    assert output == "test_output\n"
    assert len(fake.send_command_calls) == 5
    
    assert "tmux kill-session" in fake.send_command_calls[0]["Parameters"]["commands"][0]
    assert "tmux new-session" in fake.send_command_calls[1]["Parameters"]["commands"][0]
    assert "tmux" in fake.send_command_calls[2]["Parameters"]["commands"][0]
    assert "send-keys" in fake.send_command_calls[2]["Parameters"]["commands"][0]
    assert "tmux capture-pane" in fake.send_command_calls[3]["Parameters"]["commands"][0]
    assert "tmux clear-history" in fake.send_command_calls[4]["Parameters"]["commands"][0]


def test_open_session_reuses_existing_session_without_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-close-1", [_success_invocation()])
    fake._enqueue("cmd-new-1", [_success_invocation()])

    ctrl = _make_controller(fake)

    first = ctrl.open_session("test-interactive")
    second = ctrl.open_session("test-interactive")

    assert first is second
    assert len(fake.send_command_calls) == 2


def test_interactive_send_nonzero_exit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    fake = FakeSsmClient()
    fake._enqueue("cmd-close-1", [_success_invocation()])
    fake._enqueue("cmd-new-1", [_success_invocation()])
    fake._enqueue("cmd-send", [_success_invocation(stderr="tmux missing\n", exit_code=1)])

    ctrl = _make_controller(fake)
    session = ctrl.open_session("test-interactive")

    with pytest.raises(RuntimeError, match="tmux missing"):
        session.send_input("hello")
