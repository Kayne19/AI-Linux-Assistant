from __future__ import annotations

import subprocess
from dataclasses import dataclass

from eval_harness.controllers.base import SandboxController
from eval_harness.models import CommandExecutionResult
from eval_harness.orchestration.fs_helpers import apply_text_edit, read_file


@dataclass
class LocalShellController(SandboxController):
    name: str = "local_shell"

    def execute_commands(
        self,
        commands: tuple[str, ...],
        *,
        agent_id: str = "",
        session_key: str | None = None,
    ) -> tuple[CommandExecutionResult, ...]:
        results: list[CommandExecutionResult] = []
        for command in commands:
            completed = subprocess.run(
                command,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                check=False,
            )
            results.append(
                CommandExecutionResult(
                    command=command,
                    stdout=completed.stdout.decode("utf-8"),
                    stderr=completed.stderr.decode("utf-8"),
                    exit_code=completed.returncode,
                )
            )
        return tuple(results)

    def close(self) -> None:
        pass


def test_read_file_rejects_oversized_file(tmp_path) -> None:
    controller = LocalShellController()
    target = tmp_path / "large.conf"
    target.write_text("a" * (1024 * 512 + 1), encoding="utf-8")

    result = read_file(controller, "test-session", str(target))

    assert result.exit_code == 1
    assert "too large" in result.stderr.lower()


def test_apply_text_edit_supports_exact_literal_crlf_text_without_normalizing_line_endings(tmp_path) -> None:
    controller = LocalShellController()
    target = tmp_path / "windows.conf"
    original = "line1\r\nline2\r\n"
    replacement = "line1\r\nupdated\r\n"
    target.write_bytes(original.encode("utf-8"))

    read_result = read_file(controller, "test-session", str(target))
    assert read_result.exit_code == 0
    assert read_result.stdout == original

    edit_result = apply_text_edit(controller, "test-session", str(target), original, replacement)

    assert edit_result.exit_code == 0
    assert target.read_bytes() == replacement.encode("utf-8")
