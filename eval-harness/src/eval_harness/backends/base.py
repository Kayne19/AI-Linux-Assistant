from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SandboxHandle:
    handle_id: str
    kind: str
    backend_name: str
    remote_id: str
    image_id: str = ""
    local_port: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SandboxBackend(ABC):
    """Backend owns infrastructure lifecycle only."""

    name: str

    @abstractmethod
    def launch_staging(self, group_id: str, scenario_id: str, *, target_image: str | None = None) -> SandboxHandle:
        raise NotImplementedError

    @abstractmethod
    def wait_until_ready(self, handle: SandboxHandle, timeout_seconds: int = 600) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_broken_image(self, staging: SandboxHandle, group_id: str, scenario_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def launch_subject_clones(
        self,
        group_id: str,
        scenario_id: str,
        broken_image_id: str,
        subject_names: list[str],
    ) -> dict[str, SandboxHandle]:
        raise NotImplementedError

    @abstractmethod
    def destroy_handle(self, handle: SandboxHandle) -> None:
        raise NotImplementedError

    @abstractmethod
    def destroy_broken_image(self, image_id: str) -> None:
        raise NotImplementedError

    def collect_failure_diagnostics(self, handle: SandboxHandle) -> dict[str, Any]:
        del handle
        return {}

    def configure_controller_runtime(self, handle: SandboxHandle) -> dict[str, Any]:
        del handle
        return {}

    def clear_controller_runtime(self, handle: SandboxHandle) -> dict[str, Any]:
        del handle
        return {}
