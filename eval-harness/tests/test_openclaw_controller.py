from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from eval_harness.controllers.openclaw import OpenClawController, OpenClawControllerConfig


def _make_controller() -> OpenClawController:
    return OpenClawController(
        OpenClawControllerConfig(
            base_url="http://127.0.0.1:18789",
            token="token-123",
            default_session_key="session-1",
        )
    )


def _mock_post_response(text: str) -> MagicMock:
    """Return a mock requests.Response that yields ``text`` via the OpenClaw choices shape."""
    response = MagicMock()
    response.json.return_value = {
        "choices": [{"message": {"content": text, "role": "assistant"}}]
    }
    response.raise_for_status = MagicMock()
    return response


def test_send_includes_system_prompt_in_messages() -> None:
    """Test A: system_prompt is prepended as a system message in the posted payload."""
    controller = _make_controller()
    captured: list[dict] = []

    def fake_post(url, *, json=None, timeout=None):
        captured.append(json or {})
        return _mock_post_response("ack")

    with patch.object(controller.session, "post", side_effect=fake_post):
        controller.send(agent_id="x", message="hello", system_prompt="Be a user.")

    assert len(captured) == 1
    assert captured[0]["messages"] == [
        {"role": "system", "content": "Be a user."},
        {"role": "user", "content": "hello"},
    ]
    controller.close()


def test_send_without_system_prompt_sends_single_user_message() -> None:
    """Test B: omitting system_prompt produces a single user message (legacy behaviour)."""
    controller = _make_controller()
    captured: list[dict] = []

    def fake_post(url, *, json=None, timeout=None):
        captured.append(json or {})
        return _mock_post_response("ack")

    with patch.object(controller.session, "post", side_effect=fake_post):
        controller.send(agent_id="x", message="hello")

    assert len(captured) == 1
    assert captured[0]["messages"] == [{"role": "user", "content": "hello"}]
    controller.close()


def test_command_prompt_forces_non_sandbox_exec_host() -> None:
    controller = OpenClawController(
        OpenClawControllerConfig(
            base_url="http://127.0.0.1:18789",
            token="token-123",
            default_session_key="session-1",
        )
    )
    prompt = controller._command_prompt("systemctl is-active nginx")

    assert "on the target machine" in prompt
    assert "in the sandbox" not in prompt
    assert "Do not use exec host=sandbox." in prompt
    assert "use host=gateway." in prompt
    assert "Do not send /approve." in prompt
    assert "Do not rely on OpenClaw elevated exec mode." in prompt
    assert "prefix it with sudo -n." in prompt
    assert "host=auto" not in prompt

    controller.close()


def test_require_structured_command_result_rejects_approval_output() -> None:
    controller = OpenClawController(
        OpenClawControllerConfig(
            base_url="http://127.0.0.1:18789",
            token="token-123",
            default_session_key="session-1",
        )
    )
    raw_output = "/approve b8ac523b allow-once"
    parsed = controller._parse_command_result("systemctl is-active nginx", raw_output)

    with pytest.raises(RuntimeError, match="sandbox restrictions"):
        controller._require_structured_command_result("systemctl is-active nginx", raw_output, parsed)

    controller.close()


def test_require_structured_command_result_rejects_sandbox_refusal_output() -> None:
    controller = OpenClawController(
        OpenClawControllerConfig(
            base_url="http://127.0.0.1:18789",
            token="token-123",
            default_session_key="session-1",
        )
    )
    raw_output = "I need approval because the command would run in the sandbox with host=sandbox."
    parsed = controller._parse_command_result("printf READY", raw_output)

    try:
        controller._require_structured_command_result("printf READY", raw_output, parsed)
    except RuntimeError as exc:
        assert "sandbox restrictions" in str(exc)
        assert "missing_markers" in str(exc)
    else:  # pragma: no cover - assertion path
        raise AssertionError("Expected sandbox refusal output to be rejected.")

    controller.close()
