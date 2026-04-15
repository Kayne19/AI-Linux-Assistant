from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.backends.aws import AwsEc2Backend, AwsEc2BackendConfig, AwsTargetImageConfig, OpenClawRuntimeConfig
from eval_harness.backends.aws_packer import AwsPackerBuildResult
from eval_harness.backends.base import SandboxHandle


class FakeWaiter:
    def wait(self, **kwargs) -> None:
        del kwargs


class FakeEc2Client:
    def __init__(self, *, images: list[dict] | None = None):
        self.images = list(images or [])
        self.launched_images: list[str] = []
        self.run_instances_calls: list[dict] = []
        self.create_image_calls: list[dict] = []

    def describe_images(self, *, Owners=None, Filters=None, ImageIds=None):
        del Owners
        if ImageIds is not None:
            return {"Images": [item for item in self.images if item["ImageId"] in ImageIds]}
        target_image = ""
        for item in Filters or []:
            if item.get("Name") == "tag:EvalTargetImage":
                target_image = str((item.get("Values") or [""])[0])
        matched = [
            item
            for item in self.images
            if item.get("State") == "available" and item.get("Tags", {}).get("EvalTargetImage") == target_image
        ]
        return {"Images": matched}

    def run_instances(self, **params):
        self.run_instances_calls.append(dict(params))
        self.launched_images.append(str(params["ImageId"]))
        return {"Instances": [{"InstanceId": "i-1234567890", "State": {"Name": "pending"}}]}

    def create_image(self, **kwargs) -> dict:
        self.create_image_calls.append(dict(kwargs))
        return {"ImageId": "ami-broken"}

    def get_waiter(self, name: str):
        del name
        return FakeWaiter()

    def terminate_instances(self, **kwargs) -> None:
        del kwargs

    def deregister_image(self, **kwargs) -> None:
        del kwargs

    def delete_snapshot(self, **kwargs) -> None:
        del kwargs


class FakeSsmClient:
    def __init__(self, *, invocations: list[dict] | None = None):
        self.sent_commands: list[dict] = []
        self.invocations = list(invocations or [])

    def send_command(self, **kwargs):
        self.sent_commands.append(dict(kwargs))
        return {"Command": {"CommandId": "cmd-123"}}

    def get_command_invocation(self, **kwargs):
        del kwargs
        if self.invocations:
            return self.invocations.pop(0)
        return {
            "Status": "Success",
            "StatusDetails": "Success",
            "ResponseCode": 0,
            "StandardOutputContent": "ok",
            "StandardErrorContent": "",
        }


def _target_image_config() -> AwsTargetImageConfig:
    return AwsTargetImageConfig(
        target_image="debian-12-openclaw-golden",
        packer_template_dir=Path("/tmp/packer"),
        distro_vars_file=Path("/tmp/packer/distros/debian-12.pkrvars.hcl"),
    )


def _backend(
    *,
    images: list[dict] | None = None,
    golden_ami_id: str | None = None,
    builder=None,
    ssm_invocations: list[dict] | None = None,
) -> tuple[AwsEc2Backend, FakeEc2Client, FakeSsmClient]:
    ec2 = FakeEc2Client(images=images)
    ssm = FakeSsmClient(invocations=ssm_invocations)
    config = AwsEc2BackendConfig(
        region="us-west-2",
        subnet_id="subnet-123",
        security_group_ids=("sg-123",),
        instance_profile_name="EvalSSMInstanceProfile",
        default_target_image="debian-12-openclaw-golden",
        target_images={"debian-12-openclaw-golden": _target_image_config()},
        golden_ami_id=golden_ami_id,
        openclaw_eval_token="token-123",
        openclaw_runtime=OpenClawRuntimeConfig(
            provider="openai",
            model="gpt-5.4-mini",
            api_key="sk-test",
            thinking="medium",
        ),
    )
    backend = AwsEc2Backend(
        config,
        ec2_client=ec2,
        ssm_client=ssm,
        golden_image_builder=builder or (lambda request: AwsPackerBuildResult(image_id="ami-built")),
    )
    return backend, ec2, ssm


def test_launch_staging_uses_existing_tagged_golden_ami() -> None:
    backend, ec2, _ = _backend(
        images=[
            {
                "ImageId": "ami-existing",
                "CreationDate": "2026-04-14T12:00:00.000Z",
                "State": "available",
                "Tags": {"EvalTargetImage": "debian-12-openclaw-golden"},
            }
        ]
    )

    handle = backend.launch_staging("group-1", "scenario-1", target_image="debian-12-openclaw-golden")

    assert ec2.launched_images == ["ami-existing"]
    assert handle.metadata["resolved_golden_ami_id"] == "ami-existing"
    assert handle.metadata["golden_image_build_triggered"] is False
    assert handle.metadata["golden_image_build_source"] == "existing_ami"


def test_launch_staging_uses_root_volume_size_from_image_when_larger_than_default() -> None:
    backend, ec2, _ = _backend(
        images=[
            {
                "ImageId": "ami-existing",
                "CreationDate": "2026-04-14T12:00:00.000Z",
                "State": "available",
                "Tags": {"EvalTargetImage": "debian-12-openclaw-golden"},
                "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 20}}],
            }
        ]
    )

    backend.launch_staging("group-1", "scenario-1", target_image="debian-12-openclaw-golden")

    launched_call = ec2.run_instances_calls[-1]
    assert launched_call["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] == 20


def test_launch_staging_builds_missing_golden_ami() -> None:
    builder_calls = []

    def _builder(request):
        builder_calls.append(request)
        return AwsPackerBuildResult(image_id="ami-built")

    backend, ec2, _ = _backend(builder=_builder)

    handle = backend.launch_staging("group-1", "scenario-1", target_image="debian-12-openclaw-golden")

    assert len(builder_calls) == 1
    assert builder_calls[0].target_image == "debian-12-openclaw-golden"
    assert ec2.launched_images == ["ami-built"]
    assert handle.metadata["golden_image_build_triggered"] is True
    assert handle.metadata["golden_image_build_source"] == "packer"


def test_launch_staging_rejects_unsupported_target_image() -> None:
    backend, _, _ = _backend()

    with pytest.raises(ValueError, match="Unsupported target image"):
        backend.launch_staging("group-1", "scenario-1", target_image="ubuntu-2404-openclaw-golden")


def test_launch_staging_uses_legacy_default_golden_ami() -> None:
    backend, ec2, _ = _backend(golden_ami_id="ami-legacy")

    handle = backend.launch_staging("group-1", "scenario-1", target_image="debian-12-openclaw-golden")

    assert ec2.launched_images == ["ami-legacy"]
    assert handle.metadata["resolved_golden_ami_id"] == "ami-legacy"
    assert handle.metadata["golden_image_build_source"] == "legacy_config"


def test_configure_controller_runtime_writes_model_and_secret_material() -> None:
    backend, _, ssm = _backend()
    handle = SandboxHandle(handle_id="h1", kind="instance", backend_name="aws_ec2", remote_id="i-123")

    metadata = backend.configure_controller_runtime(handle)

    assert metadata["openclaw_runtime_model"] == "openai/gpt-5.4-mini"
    assert metadata["openclaw_runtime_thinking"] == "medium"
    assert metadata["openclaw_gateway_ready"] is True
    assert metadata["openclaw_model_probe_passed"] is True
    assert metadata["openclaw_command_probe_passed"] is True
    assert metadata["openclaw_setup_capability_probe_passed"] is True
    assert len(metadata["openclaw_runtime_phase_summaries"]) == 6
    assert len(ssm.sent_commands) == 6
    rendered = "\n".join(command for entry in ssm.sent_commands for command in entry["Parameters"]["commands"])
    assert '"primary": "openai/gpt-5.4-mini"' in rendered
    assert '"thinkingDefault": "medium"' in rendered
    assert '"elevatedDefault": "full"' in rendered
    assert '"enabled": true' in rendered
    assert '"webchat": [' in rendered
    assert rendered.count('"webchat": [') == 1
    assert "OPENAI_API_KEY=sk-test" in rendered
    assert "Destructive changes inside the sandbox are intentional and authorized." in rendered
    assert "This is not a live service, production host, or customer environment." in rendered
    assert "Elevated host execution is already authorized for this session." in rendered
    assert "Do not rely on OpenClaw elevated exec mode for privileged work." in rendered
    assert "prefix it with sudo -n." in rendered
    assert "Do not undo, clean up, or repair the sabotage after you verify it." in rendered
    assert "Leave the machine in the final broken state when you reply." in rendered
    assert "Do not use exec host=sandbox." in rendered
    assert "Run the exact shell command below on the target machine" in rendered
    assert "use host=gateway." in rendered
    assert "Gateway HTTP response status" in rendered
    assert "Reply with READY." in rendered
    assert "NoNewPrivileges=true" not in rendered
    assert "ProtectSystem=strict" not in rendered
    assert "ReadWritePaths=/home/eval /etc/openclaw" not in rendered


def test_configure_controller_runtime_reports_model_probe_failure_category() -> None:
    backend, _, _ = _backend(
        ssm_invocations=[
            {
                "Status": "Success",
                "StatusDetails": "Success",
                "ResponseCode": 0,
                "StandardOutputContent": "wrote runtime files",
                "StandardErrorContent": "",
            },
            {
                "Status": "Success",
                "StatusDetails": "Success",
                "ResponseCode": 0,
                "StandardOutputContent": "active (running)\n127.0.0.1:18789",
                "StandardErrorContent": "",
            },
            {
                "Status": "Success",
                "StatusDetails": "Success",
                "ResponseCode": 0,
                "StandardOutputContent": "Gateway HTTP response status=400",
                "StandardErrorContent": "",
            },
            {
                "Status": "Failed",
                "StatusDetails": "Failed",
                "ResponseCode": 1,
                "StandardOutputContent": "HTTPError: 401\nAuthenticationError: Invalid API key",
                "StandardErrorContent": "",
            },
        ]
    )
    handle = SandboxHandle(handle_id="h1", kind="instance", backend_name="aws_ec2", remote_id="i-123")

    with pytest.raises(RuntimeError, match="during model_probe: category=invalid_api_key"):
        backend.configure_controller_runtime(handle)


def test_clear_controller_runtime_removes_runtime_files() -> None:
    backend, _, ssm = _backend()
    handle = SandboxHandle(handle_id="h1", kind="instance", backend_name="aws_ec2", remote_id="i-123")

    metadata = backend.clear_controller_runtime(handle)

    assert metadata["openclaw_runtime_cleared"] is True
    commands = ssm.sent_commands[-1]["Parameters"]["commands"]
    rendered = commands[0]
    assert '"port": 18789' in rendered
    assert 'OPENCLAW_GATEWAY_TOKEN=token-123' in rendered
    assert 'systemctl stop openclaw-gateway.service' in rendered


def test_service_restart_script_does_not_require_port_binding() -> None:
    """service_restart must only wait for systemd active; port readiness belongs in gateway_probe."""
    backend, _, ssm = _backend()
    handle = SandboxHandle(handle_id="h1", kind="instance", backend_name="aws_ec2", remote_id="i-123")

    backend.configure_controller_runtime(handle)

    service_restart_cmd = ssm.sent_commands[1]["Parameters"]["commands"][0]
    assert "ss -ltn" not in service_restart_cmd, (
        "Port listening check must not be in service_restart — it was causing false-failures "
        "because OpenClaw binds :18789 tens of seconds after systemd reports active"
    )
    assert "is-active" in service_restart_cmd


def test_auth_error_classifier_does_not_match_openclaw_startup_log() -> None:
    """'resolving authentication…' is a normal OpenClaw startup log line; must not be auth_error."""
    backend, _, _ = _backend()
    # This phrase appears in `systemctl status` journal output on every clean start.
    startup_text = "active\nActiveState=active\n2026-04-15 [gateway] resolving authentication…\n2026-04-15 [gateway] starting..."
    category = backend._classify_runtime_phase_text("service_restart", startup_text)
    assert category != "auth_error", (
        "OpenClaw's startup log 'resolving authentication…' must not trigger auth_error classification"
    )


def test_gateway_probe_script_retries_and_catches_broad_exceptions() -> None:
    backend, _, ssm = _backend()
    handle = SandboxHandle(handle_id="h1", kind="instance", backend_name="aws_ec2", remote_id="i-123")

    backend.configure_controller_runtime(handle)

    # Phase 3 is gateway_probe (write_runtime_files, service_restart, gateway_probe, model_probe).
    gateway_probe_cmd = ssm.sent_commands[2]["Parameters"]["commands"][0]
    assert "for _ in range(" in gateway_probe_cmd, (
        "gateway_probe must retry; a single urlopen is too tight for a freshly-restarted Node gateway"
    )
    assert "except Exception" in gateway_probe_cmd, (
        "gateway_probe must catch URLError/socket.timeout, not only urllib.error.HTTPError"
    )
    assert "time.sleep(" in gateway_probe_cmd
    # Must still emit the classifier sentinel so _classify_runtime_phase_text marks the phase ready.
    assert "Gateway HTTP response status=" in gateway_probe_cmd


def test_command_probe_script_requires_structured_host_exec_result() -> None:
    backend, _, ssm = _backend()
    handle = SandboxHandle(handle_id="h1", kind="instance", backend_name="aws_ec2", remote_id="i-123")

    backend.configure_controller_runtime(handle)

    command_probe_cmd = ssm.sent_commands[4]["Parameters"]["commands"][0]
    assert '"model": "openclaw/verifier"' in command_probe_cmd
    assert "Run the exact shell command below on the target machine" in command_probe_cmd
    assert "Do not use exec host=sandbox." in command_probe_cmd
    assert "use host=gateway." in command_probe_cmd
    assert "Do not send /approve." in command_probe_cmd
    assert "Do not rely on OpenClaw elevated exec mode." in command_probe_cmd
    assert "Missing structured markers" in command_probe_cmd
    assert "Unexpected verifier stdout" in command_probe_cmd


def test_setup_capability_probe_script_requires_privileged_host_exec_result() -> None:
    backend, _, ssm = _backend()
    handle = SandboxHandle(handle_id="h1", kind="instance", backend_name="aws_ec2", remote_id="i-123")

    backend.configure_controller_runtime(handle)

    setup_probe_cmd = ssm.sent_commands[5]["Parameters"]["commands"][0]
    assert '"model": "openclaw/setup"' in setup_probe_cmd
    assert "Elevated host execution is already authorized for this session." in setup_probe_cmd
    assert "Do not send /approve." in setup_probe_cmd
    assert "sudo -n sh -lc" in setup_probe_cmd
    assert "Unexpected setup probe stdout" in setup_probe_cmd


def test_classifier_marks_command_probe_sandbox_refusal() -> None:
    backend, _, _ = _backend()
    text = "I need approval because this would execute in the sandbox with host=sandbox."
    category = backend._classify_runtime_phase_text("command_probe", text)
    assert category == "sandbox_refusal"


def test_classifier_marks_setup_capability_probe_sandbox_refusal() -> None:
    backend, _, _ = _backend()
    text = "Blocked by the sandbox. elevated exec is disabled here. /approve abc123 allow-once"
    category = backend._classify_runtime_phase_text("setup_capability_probe", text)
    assert category == "sandbox_refusal"


def test_classifier_marks_no_new_privileges_permission_denied() -> None:
    backend, _, _ = _backend()
    text = 'sudo: The "no new privileges" flag is set, which prevents sudo from running as root.'
    category = backend._classify_runtime_phase_text("setup_capability_probe", text)
    assert category == "permission_denied"


def test_configure_controller_runtime_rejects_empty_token() -> None:
    ec2 = FakeEc2Client()
    ssm = FakeSsmClient()
    config = AwsEc2BackendConfig(
        region="us-west-2",
        subnet_id="subnet-123",
        security_group_ids=("sg-123",),
        instance_profile_name="EvalSSMInstanceProfile",
        default_target_image="debian-12-openclaw-golden",
        target_images={"debian-12-openclaw-golden": _target_image_config()},
        openclaw_eval_token="   ",  # whitespace-only → invalid
        openclaw_runtime=OpenClawRuntimeConfig(
            provider="openai",
            model="gpt-5.4-mini",
            api_key="sk-test",
        ),
    )
    backend = AwsEc2Backend(config, ec2_client=ec2, ssm_client=ssm)
    handle = SandboxHandle(handle_id="h1", kind="instance", backend_name="aws_ec2", remote_id="i-123")

    with pytest.raises(RuntimeError, match="openclaw_eval_token must be configured"):
        backend.configure_controller_runtime(handle)
    # Must fail before sending any SSM command.
    assert ssm.sent_commands == []


def test_configure_controller_runtime_attaches_partial_phase_summaries_on_failure() -> None:
    backend, _, _ = _backend(
        ssm_invocations=[
            {
                "Status": "Success",
                "StatusDetails": "Success",
                "ResponseCode": 0,
                "StandardOutputContent": "wrote runtime files",
                "StandardErrorContent": "",
            },
            {
                "Status": "Failed",
                "StatusDetails": "Failed",
                "ResponseCode": 1,
                "StandardOutputContent": "",
                "StandardErrorContent": "systemctl: failed to restart",
            },
        ]
    )
    handle = SandboxHandle(handle_id="h1", kind="instance", backend_name="aws_ec2", remote_id="i-123")

    with pytest.raises(RuntimeError, match="during service_restart") as exc_info:
        backend.configure_controller_runtime(handle)

    # Caller must be able to recover the phases attempted so far for diagnostic metadata.
    summaries = getattr(exc_info.value, "phase_summaries", None)
    assert summaries is not None
    assert [s["phase"] for s in summaries] == ["write_runtime_files", "service_restart"]
    assert summaries[-1]["category"] != "empty_output"


def test_launch_subject_clones_terminates_already_launched_on_failure() -> None:
    terminated: list[str] = []

    class FlakyEc2(FakeEc2Client):
        def __init__(self) -> None:
            super().__init__()
            self._launch_count = 0

        def run_instances(self, **params):
            self._launch_count += 1
            if self._launch_count >= 2:
                raise RuntimeError("EC2 quota exceeded")
            return super().run_instances(**params)

        def terminate_instances(self, **kwargs) -> None:
            terminated.extend(kwargs.get("InstanceIds", []))

    ec2 = FlakyEc2()
    ssm = FakeSsmClient()
    config = AwsEc2BackendConfig(
        region="us-west-2",
        subnet_id="subnet-123",
        security_group_ids=("sg-123",),
        instance_profile_name="EvalSSMInstanceProfile",
        default_target_image="debian-12-openclaw-golden",
        target_images={"debian-12-openclaw-golden": _target_image_config()},
        openclaw_eval_token="token-123",
        openclaw_runtime=OpenClawRuntimeConfig(provider="openai", model="gpt-5.4-mini", api_key="sk-test"),
    )
    backend = AwsEc2Backend(config, ec2_client=ec2, ssm_client=ssm)

    with pytest.raises(RuntimeError, match="EC2 quota exceeded"):
        backend.launch_subject_clones("group-1", "scenario-1", "ami-broken", ["subject-a", "subject-b"])

    # The first subject launched successfully before the second failed; it must be terminated.
    assert terminated == ["i-1234567890"]


def test_create_broken_image_deregisters_on_timeout() -> None:
    images_state = {"ami-broken": {"State": "pending", "BlockDeviceMappings": []}}
    deregistered: list[str] = []

    class SlowEc2(FakeEc2Client):
        def create_image(self, **kwargs) -> dict:
            del kwargs
            return {"ImageId": "ami-broken"}

        def describe_images(self, *, Owners=None, Filters=None, ImageIds=None):
            if ImageIds is not None:
                return {"Images": [dict({"ImageId": k, **v}) for k, v in images_state.items() if k in ImageIds]}
            return super().describe_images(Owners=Owners, Filters=Filters, ImageIds=ImageIds)

        def deregister_image(self, **kwargs) -> None:
            deregistered.append(str(kwargs.get("ImageId", "")))

    ec2 = SlowEc2()
    ssm = FakeSsmClient()
    config = AwsEc2BackendConfig(
        region="us-west-2",
        subnet_id="subnet-123",
        security_group_ids=("sg-123",),
        instance_profile_name="EvalSSMInstanceProfile",
        default_target_image="debian-12-openclaw-golden",
        target_images={"debian-12-openclaw-golden": _target_image_config()},
        image_wait_timeout_seconds=0,  # forces immediate timeout
        openclaw_eval_token="token-123",
        openclaw_runtime=OpenClawRuntimeConfig(provider="openai", model="gpt-5.4-mini", api_key="sk-test"),
    )
    backend = AwsEc2Backend(config, ec2_client=ec2, ssm_client=ssm)
    staging = SandboxHandle(handle_id="s1", kind="instance", backend_name="aws_ec2", remote_id="i-staging")

    with pytest.raises(TimeoutError):
        backend.create_broken_image(staging, "group-1", "scenario-1")

    assert deregistered == ["ami-broken"]


def test_request_broken_image_returns_image_id_before_waiting() -> None:
    backend, ec2, _ = _backend()
    staging = SandboxHandle(handle_id="s1", kind="instance", backend_name="aws_ec2", remote_id="i-staging")

    image_id = backend.request_broken_image(staging, "group-1", "scenario-1")

    assert image_id == "ami-broken"
    assert ec2.create_image_calls[-1]["InstanceId"] == "i-staging"
    assert ec2.create_image_calls[-1]["Name"] == "eval-broken-scenario-1-group-1"


def test_wait_for_broken_image_emits_progress_until_available(monkeypatch: pytest.MonkeyPatch) -> None:
    class ProgressEc2(FakeEc2Client):
        def __init__(self) -> None:
            super().__init__(images=[{"ImageId": "ami-broken", "State": "pending", "BlockDeviceMappings": []}])
            self._states = iter(("pending", "available"))

        def describe_images(self, *, Owners=None, Filters=None, ImageIds=None):
            if ImageIds is not None:
                state = next(self._states, "available")
                return {"Images": [{"ImageId": "ami-broken", "State": state, "BlockDeviceMappings": []}]}
            return super().describe_images(Owners=Owners, Filters=Filters, ImageIds=ImageIds)

    ec2 = ProgressEc2()
    ssm = FakeSsmClient()
    config = AwsEc2BackendConfig(
        region="us-west-2",
        subnet_id="subnet-123",
        security_group_ids=("sg-123",),
        instance_profile_name="EvalSSMInstanceProfile",
        default_target_image="debian-12-openclaw-golden",
        target_images={"debian-12-openclaw-golden": _target_image_config()},
        image_wait_timeout_seconds=31,
        openclaw_eval_token="token-123",
        openclaw_runtime=OpenClawRuntimeConfig(provider="openai", model="gpt-5.4-mini", api_key="sk-test"),
    )
    backend = AwsEc2Backend(config, ec2_client=ec2, ssm_client=ssm)
    progress_updates: list[dict] = []
    monkeypatch.setattr("eval_harness.backends.aws.time.sleep", lambda _: None)

    backend.wait_for_broken_image("ami-broken", progress_callback=lambda metadata: progress_updates.append(dict(metadata)))

    assert [item["broken_image_state"] for item in progress_updates] == ["pending", "available"]
    assert all(item["broken_image_id"] == "ami-broken" for item in progress_updates)
