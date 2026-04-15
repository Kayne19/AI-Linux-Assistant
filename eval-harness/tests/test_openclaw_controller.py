from __future__ import annotations

from eval_harness.controllers.openclaw import OpenClawController, OpenClawControllerConfig


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
    assert "host=auto" not in prompt

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
