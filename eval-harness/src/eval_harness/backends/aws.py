from __future__ import annotations

import time
from dataclasses import dataclass, field

from .base import SandboxBackend, SandboxHandle
from ..runtime.ssm import wait_for_ssm_online

try:
    import boto3
    from botocore.exceptions import WaiterError
except ImportError:  # pragma: no cover - optional dependency
    boto3 = None
    WaiterError = Exception


def _require_boto3() -> None:
    if boto3 is None:  # pragma: no cover - depends on optional dependency
        raise RuntimeError("boto3 is required for the AWS backend. Install eval-harness[aws].")


@dataclass(frozen=True)
class AwsEc2BackendConfig:
    region: str
    subnet_id: str
    security_group_ids: tuple[str, ...]
    instance_profile_name: str
    golden_ami_id: str
    instance_type: str = "t3.small"
    staging_name_prefix: str = "eval-staging"
    clone_name_prefix: str = "eval-clone"
    default_tags: dict[str, str] = field(default_factory=dict)
    root_volume_size_gb: int = 16
    image_wait_timeout_seconds: int = 1800
    ssm_wait_timeout_seconds: int = 600
    terminate_wait_seconds: int = 300
    use_spot: bool = False


class AwsEc2Backend(SandboxBackend):
    """
    AWS-only v1 backend.

    Backend responsibilities:
    - launch staging instance from the golden AMI
    - wait for SSM readiness
    - create a broken image after staging verification succeeds
    - launch one clone per benchmark subject from that broken image
    - tear down instances and deregister transient images
    """

    name = "aws_ec2"

    def __init__(self, config: AwsEc2BackendConfig):
        _require_boto3()
        self.config = config
        self.ec2 = boto3.client("ec2", region_name=config.region)
        self.ssm = boto3.client("ssm", region_name=config.region)

    def _base_tags(self, group_id: str, scenario_id: str, role: str, extra: dict[str, str] | None = None) -> list[dict[str, str]]:
        tags = {
            "EvalHarness": "true",
            "EvalGroupId": group_id,
            "EvalScenarioId": scenario_id,
            "EvalRole": role,
            **self.config.default_tags,
        }
        if extra:
            tags.update(extra)
        return [{"Key": key, "Value": value} for key, value in tags.items()]

    def _launch_instance(self, *, image_id: str, name: str, tags: list[dict[str, str]]) -> SandboxHandle:
        params: dict[str, object] = {
            "ImageId": image_id,
            "InstanceType": self.config.instance_type,
            "SubnetId": self.config.subnet_id,
            "SecurityGroupIds": list(self.config.security_group_ids),
            "IamInstanceProfile": {"Name": self.config.instance_profile_name},
            "MinCount": 1,
            "MaxCount": 1,
            "TagSpecifications": [{"ResourceType": "instance", "Tags": tags}],
            "BlockDeviceMappings": [
                {
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "DeleteOnTermination": True,
                        "VolumeSize": self.config.root_volume_size_gb,
                        "VolumeType": "gp3",
                    },
                }
            ],
        }
        if self.config.use_spot:
            params["InstanceMarketOptions"] = {"MarketType": "spot"}
        response = self.ec2.run_instances(**params)
        instance = response["Instances"][0]
        instance_id = instance["InstanceId"]
        return SandboxHandle(
            handle_id=name,
            kind="instance",
            backend_name=self.name,
            remote_id=instance_id,
            image_id=image_id,
            metadata={"state": instance.get("State", {}).get("Name", ""), "launched_image_id": image_id},
        )

    def launch_staging(self, group_id: str, scenario_id: str) -> SandboxHandle:
        name = f"{self.config.staging_name_prefix}-{group_id}"
        tags = self._base_tags(group_id, scenario_id, "staging", {"Name": name})
        return self._launch_instance(image_id=self.config.golden_ami_id, name=name, tags=tags)

    def wait_until_ready(self, handle: SandboxHandle, timeout_seconds: int = 600) -> None:
        waiter = self.ec2.get_waiter("instance_running")
        waiter.wait(InstanceIds=[handle.remote_id], WaiterConfig={"Delay": 10, "MaxAttempts": max(1, timeout_seconds // 10)})
        wait_for_ssm_online(self.ssm, handle.remote_id, timeout_seconds=timeout_seconds)

    def create_broken_image(self, staging: SandboxHandle, group_id: str, scenario_id: str) -> str:
        image_name = f"eval-broken-{scenario_id}-{group_id}"
        response = self.ec2.create_image(
            InstanceId=staging.remote_id,
            Name=image_name,
            Description=f"Transient broken image for eval group {group_id}",
            NoReboot=True,
            TagSpecifications=[{"ResourceType": "image", "Tags": self._base_tags(group_id, scenario_id, "broken-image", {"Name": image_name})}],
        )
        image_id = response["ImageId"]
        deadline = time.time() + self.config.image_wait_timeout_seconds
        while time.time() < deadline:
            state = self.ec2.describe_images(ImageIds=[image_id])["Images"][0]["State"]
            if state == "available":
                return image_id
            if state in {"failed", "deregistered"}:
                raise RuntimeError(f"Broken image {image_id} entered terminal state {state}.")
            time.sleep(15)
        raise TimeoutError(f"Timed out waiting for broken image {image_id} to become available.")

    def launch_subject_clones(
        self,
        group_id: str,
        scenario_id: str,
        broken_image_id: str,
        subject_names: list[str],
    ) -> dict[str, SandboxHandle]:
        handles: dict[str, SandboxHandle] = {}
        for subject_name in subject_names:
            name = f"{self.config.clone_name_prefix}-{subject_name}-{group_id}"
            tags = self._base_tags(group_id, scenario_id, "subject-clone", {"Name": name, "EvalSubject": subject_name})
            handle = self._launch_instance(image_id=broken_image_id, name=name, tags=tags)
            handles[subject_name] = handle
        return handles

    def destroy_handle(self, handle: SandboxHandle) -> None:
        self.ec2.terminate_instances(InstanceIds=[handle.remote_id])
        waiter = self.ec2.get_waiter("instance_terminated")
        try:
            waiter.wait(
                InstanceIds=[handle.remote_id],
                WaiterConfig={"Delay": 10, "MaxAttempts": max(1, self.config.terminate_wait_seconds // 10)},
            )
        except WaiterError as exc:  # pragma: no cover - depends on AWS
            raise RuntimeError(f"Timed out terminating instance {handle.remote_id}") from exc

    def destroy_broken_image(self, image_id: str) -> None:
        images = self.ec2.describe_images(ImageIds=[image_id]).get("Images", [])
        if not images:
            return
        snapshot_ids: list[str] = []
        for mapping in images[0].get("BlockDeviceMappings", []):
            ebs = mapping.get("Ebs") or {}
            snapshot_id = ebs.get("SnapshotId")
            if snapshot_id:
                snapshot_ids.append(snapshot_id)

        self.ec2.deregister_image(ImageId=image_id)
        for snapshot_id in snapshot_ids:
            self.ec2.delete_snapshot(SnapshotId=snapshot_id)
