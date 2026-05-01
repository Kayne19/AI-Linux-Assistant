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

from eval_harness.backends.aws import AwsEc2Backend, AwsEc2BackendConfig, AwsTargetImageConfig
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
        target_image="debian-12-ssm-golden",
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
        default_target_image="debian-12-ssm-golden",
        target_images={"debian-12-ssm-golden": _target_image_config()},
        golden_ami_id=golden_ami_id,
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
                "Tags": {"EvalTargetImage": "debian-12-ssm-golden"},
            }
        ]
    )

    handle = backend.launch_staging("group-1", "scenario-1", target_image="debian-12-ssm-golden")

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
                "Tags": {"EvalTargetImage": "debian-12-ssm-golden"},
                "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 20}}],
            }
        ]
    )

    backend.launch_staging("group-1", "scenario-1", target_image="debian-12-ssm-golden")

    launched_call = ec2.run_instances_calls[-1]
    assert launched_call["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] == 20


def test_launch_staging_builds_missing_golden_ami() -> None:
    builder_calls = []

    def _builder(request):
        builder_calls.append(request)
        return AwsPackerBuildResult(image_id="ami-built")

    backend, ec2, _ = _backend(builder=_builder)

    handle = backend.launch_staging("group-1", "scenario-1", target_image="debian-12-ssm-golden")

    assert len(builder_calls) == 1
    assert builder_calls[0].target_image == "debian-12-ssm-golden"
    assert ec2.launched_images == ["ami-built"]
    assert handle.metadata["golden_image_build_triggered"] is True
    assert handle.metadata["golden_image_build_source"] == "packer"


def test_launch_staging_rejects_unsupported_target_image() -> None:
    backend, _, _ = _backend()

    with pytest.raises(ValueError, match="Unsupported target image"):
        backend.launch_staging("group-1", "scenario-1", target_image="ubuntu-2404-unknown")


def test_launch_staging_uses_legacy_default_golden_ami() -> None:
    backend, ec2, _ = _backend(golden_ami_id="ami-legacy")

    handle = backend.launch_staging("group-1", "scenario-1", target_image="debian-12-ssm-golden")

    assert ec2.launched_images == ["ami-legacy"]
    assert handle.metadata["resolved_golden_ami_id"] == "ami-legacy"
    assert handle.metadata["golden_image_build_source"] == "legacy_config"


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
        default_target_image="debian-12-ssm-golden",
        target_images={"debian-12-ssm-golden": _target_image_config()},
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
        default_target_image="debian-12-ssm-golden",
        target_images={"debian-12-ssm-golden": _target_image_config()},
        image_wait_timeout_seconds=0,  # forces immediate timeout
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
        default_target_image="debian-12-ssm-golden",
        target_images={"debian-12-ssm-golden": _target_image_config()},
        image_wait_timeout_seconds=31,
    )
    backend = AwsEc2Backend(config, ec2_client=ec2, ssm_client=ssm)
    progress_updates: list[dict] = []
    monkeypatch.setattr("eval_harness.backends.aws.time.sleep", lambda _: None)

    backend.wait_for_broken_image("ami-broken", progress_callback=lambda metadata: progress_updates.append(dict(metadata)))

    assert [item["broken_image_state"] for item in progress_updates] == ["pending", "available"]
    assert all(item["broken_image_id"] == "ami-broken" for item in progress_updates)
