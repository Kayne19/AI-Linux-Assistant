from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from eval_harness.adapters.base import SolverAdapter, SolverSession
from eval_harness.backends.base import SandboxBackend, SandboxHandle
from eval_harness.models import (
    AdapterTurnResult,
    RunEvent,
    RunEventType,
    ScenarioSpec,
    VariantLifecycle,
    VariantSpec,
    VerificationCheck,
    VerificationResult,
)
from eval_harness.orchestrator import EvalOrchestrator


def _scenario(*, variants: tuple[VariantSpec, ...] | None = None) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id="svc-nginx-001",
        title="nginx broken",
        summary="Example",
        target_image="debian-12-openclaw-golden",
        setup_steps=("break nginx config",),
        broken_state_checks=(VerificationCheck(name="broken", command="systemctl is-active nginx"),),
        resolution_checks=(VerificationCheck(name="fixed", command="systemctl is-active nginx"),),
        opening_user_message="nginx will not start",
        turn_budget=4,
        variants=variants or (VariantSpec(name="regular", solver_mode="off"),),
    )


@dataclass
class FakeSession(SolverSession):
    status: str = "completed"
    assistant_message: str = "That should fix it."
    seed_calls: list[tuple[str, ...]] = field(default_factory=list)
    submitted_messages: list[tuple[str, str | None]] = field(default_factory=list)

    def seed_context(self, context_seed):
        self.seed_calls.append(tuple(turn.content for turn in context_seed))

    def submit_user_message(self, message: str, *, mode_override: str | None = None) -> AdapterTurnResult:
        self.submitted_messages.append((message, mode_override))
        return AdapterTurnResult(
            user_message=message,
            assistant_message=self.assistant_message,
            run_id="run-1",
            status=self.status,
            terminal_event_type="done" if self.status == "completed" else "error",
            events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
        )

    def close(self) -> dict[str, str]:
        return {"closed": "true"}


@dataclass
class FakeAdapter(SolverAdapter):
    name: str = "fake_adapter"
    session_factory: Callable[[], FakeSession] | None = None
    created_variants: list[str] = field(default_factory=list)

    def create_session(self, scenario, group_id, variant):
        del scenario, group_id
        self.created_variants.append(variant.name)
        factory = self.session_factory or (lambda: FakeSession())
        return factory()


@dataclass
class FakeController:
    name: str = "fake_controller"
    send_responses: list[str] = field(default_factory=lambda: ["setup-complete"])
    verification_results: list[VerificationResult] = field(default_factory=list)
    sent_messages: list[str] = field(default_factory=list)
    closed: bool = False

    def send(self, *, agent_id: str, message: str, session_key: str | None = None) -> str:
        del agent_id, session_key
        self.sent_messages.append(message)
        if self.send_responses:
            return self.send_responses.pop(0)
        return ""

    def run_verification(self, check: VerificationCheck, *, agent_id: str, session_key: str | None = None) -> VerificationResult:
        del check, agent_id, session_key
        if self.verification_results:
            return self.verification_results.pop(0)
        return VerificationResult(check_name="default", command="true", success=True, output="ok")

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeBackend(SandboxBackend):
    name: str = "fake_backend"
    wait_calls: list[str] = field(default_factory=list)
    destroyed_handles: list[str] = field(default_factory=list)
    destroyed_images: list[str] = field(default_factory=list)
    created_broken_images: list[str] = field(default_factory=list)

    def launch_staging(self, group_id: str, scenario_id: str) -> SandboxHandle:
        return SandboxHandle(
            handle_id=f"{group_id}-staging",
            kind="instance",
            backend_name=self.name,
            remote_id=f"instance-staging-{scenario_id}",
            image_id="golden-ami",
        )

    def wait_until_ready(self, handle: SandboxHandle, timeout_seconds: int = 600) -> None:
        del timeout_seconds
        self.wait_calls.append(handle.remote_id)

    def create_broken_image(self, staging: SandboxHandle, group_id: str, scenario_id: str) -> str:
        del staging, scenario_id
        image_id = f"broken-{group_id}"
        self.created_broken_images.append(image_id)
        return image_id

    def launch_variant_clones(self, group_id: str, scenario_id: str, broken_image_id: str, variants: list[str]) -> dict[str, SandboxHandle]:
        del scenario_id, broken_image_id
        return {
            variant: SandboxHandle(
                handle_id=f"{group_id}-{variant}",
                kind="instance",
                backend_name=self.name,
                remote_id=f"instance-{variant}",
                image_id="broken-ami",
            )
            for variant in variants
        }

    def destroy_handle(self, handle: SandboxHandle) -> None:
        self.destroyed_handles.append(handle.remote_id)

    def destroy_broken_image(self, image_id: str) -> None:
        self.destroyed_images.append(image_id)


def test_setup_failure_short_circuits_variants() -> None:
    backend = FakeBackend()
    staging_controller = FakeController(
        verification_results=[
            VerificationResult(check_name="broken", command="check", success=False, output="still healthy")
        ]
    )
    controllers: list[FakeController] = [staging_controller]
    adapter = FakeAdapter()
    scenario = _scenario()

    def controller_factory(handle: SandboxHandle, session_name: str):
        del handle, session_name
        controller = controllers.pop(0)
        return controller

    pack = EvalOrchestrator(
        backend=backend,
        controller_factory=controller_factory,
        adapter=adapter,
    ).run_group(scenario, group_id="group-1")

    assert pack.metadata["status"] == VariantLifecycle.SETUP_FAILED.value
    assert pack.variant_artifacts == ()
    assert adapter.created_variants == []
    assert backend.created_broken_images == []
    assert "instance-staging-svc-nginx-001" in backend.destroyed_handles


def test_resolution_failure_is_separate_from_variant_lifecycle() -> None:
    backend = FakeBackend()
    staging_controller = FakeController(
        verification_results=[
            VerificationResult(check_name="broken", command="check", success=True, output="failed")
        ]
    )
    variant_controller = FakeController(
        verification_results=[
            VerificationResult(check_name="fixed", command="check", success=False, output="failed")
        ]
    )
    controllers = [staging_controller, variant_controller]
    adapter = FakeAdapter()
    scenario = _scenario()

    def controller_factory(handle: SandboxHandle, session_name: str):
        del handle, session_name
        return controllers.pop(0)

    pack = EvalOrchestrator(
        backend=backend,
        controller_factory=controller_factory,
        adapter=adapter,
    ).run_group(scenario, group_id="group-2")

    assert pack.metadata["status"] == VariantLifecycle.COMPLETED.value
    assert len(pack.variant_artifacts) == 1
    artifact = pack.variant_artifacts[0]
    assert artifact.lifecycle == VariantLifecycle.COMPLETED
    assert artifact.metadata["repair_success"] is False
    assert artifact.resolution_results[0].success is False


def test_cleanup_destroys_variant_instances_and_broken_image() -> None:
    backend = FakeBackend()
    staging_controller = FakeController(
        verification_results=[
            VerificationResult(check_name="broken", command="check", success=True, output="failed")
        ]
    )
    variant_controllers = [
        FakeController(
            verification_results=[
                VerificationResult(check_name="fixed", command="check", success=True, output="active")
            ]
        ),
        FakeController(
            verification_results=[
                VerificationResult(check_name="fixed", command="check", success=True, output="active")
            ]
        ),
    ]
    controllers = [staging_controller, *variant_controllers]
    adapter = FakeAdapter(session_factory=lambda: FakeSession())
    scenario = _scenario(
        variants=(
            VariantSpec(name="regular", solver_mode="off"),
            VariantSpec(name="magi_full", solver_mode="full"),
        )
    )

    def controller_factory(handle: SandboxHandle, session_name: str):
        del handle, session_name
        return controllers.pop(0)

    pack = EvalOrchestrator(
        backend=backend,
        controller_factory=controller_factory,
        adapter=adapter,
    ).run_group(scenario, group_id="group-3")

    assert pack.metadata["cleanup_complete"] is True
    assert sorted(backend.destroyed_handles) == [
        "instance-magi_full",
        "instance-regular",
        "instance-staging-svc-nginx-001",
    ]
    assert backend.destroyed_images == ["broken-group-3"]
