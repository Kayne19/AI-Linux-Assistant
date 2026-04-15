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
    def __init__(self):
        self.sent_commands: list[dict] = []

    def send_command(self, **kwargs):
        self.sent_commands.append(dict(kwargs))
        return {"Command": {"CommandId": "cmd-123"}}

    def get_command_invocation(self, **kwargs):
        del kwargs
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
) -> tuple[AwsEc2Backend, FakeEc2Client, FakeSsmClient]:
    ec2 = FakeEc2Client(images=images)
    ssm = FakeSsmClient()
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
    commands = ssm.sent_commands[-1]["Parameters"]["commands"]
    rendered = "\n".join(commands)
    assert '"primary": "openai/gpt-5.4-mini"' in rendered
    assert '"thinkingDefault": "medium"' in rendered
    assert "OPENAI_API_KEY=sk-test" in rendered
    assert "Destructive changes inside the sandbox are intentional and authorized." in rendered
    assert "Do not use exec host=sandbox." in rendered


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
