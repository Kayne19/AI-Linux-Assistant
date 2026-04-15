from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .aws_packer import AwsPackerBuildRequest, build_golden_ami
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
class AwsTargetImageConfig:
    target_image: str
    packer_template_dir: Path
    distro_vars_file: Path
    openclaw_version: str = "2026.4.11"
    node_major_version: str = "24"
    packer_bin: str = "packer"


@dataclass(frozen=True)
class ResolvedGoldenImage:
    image_id: str
    target_image: str
    build_triggered: bool
    build_source: str


@dataclass(frozen=True)
class AwsEc2BackendConfig:
    region: str
    subnet_id: str
    security_group_ids: tuple[str, ...]
    instance_profile_name: str
    instance_type: str = "t3.small"
    staging_name_prefix: str = "eval-staging"
    clone_name_prefix: str = "eval-clone"
    default_tags: dict[str, str] = field(default_factory=dict)
    root_volume_size_gb: int = 16
    image_wait_timeout_seconds: int = 1800
    ssm_wait_timeout_seconds: int = 600
    terminate_wait_seconds: int = 300
    use_spot: bool = False
    vpc_id: str = ""
    default_target_image: str = ""
    target_images: dict[str, AwsTargetImageConfig] = field(default_factory=dict)
    golden_ami_id: str | None = None
    golden_image_build_timeout_seconds: int = 3600
    openclaw_eval_token: str = ""


class AwsEc2Backend(SandboxBackend):
    """
    AWS-only v1 backend.

    Backend responsibilities:
    - launch staging instance from a resolved golden AMI for the requested target image
    - wait for SSM readiness
    - create a broken image after staging verification succeeds
    - launch one clone per benchmark subject from that broken image
    - tear down instances and deregister transient images
    """

    name = "aws_ec2"

    def __init__(
        self,
        config: AwsEc2BackendConfig,
        *,
        ec2_client: Any | None = None,
        ssm_client: Any | None = None,
        golden_image_builder: Callable[[AwsPackerBuildRequest], Any] = build_golden_ami,
    ):
        if ec2_client is None or ssm_client is None:
            _require_boto3()
        self.config = config
        self.ec2 = ec2_client or boto3.client("ec2", region_name=config.region)
        self.ssm = ssm_client or boto3.client("ssm", region_name=config.region)
        self._golden_image_builder = golden_image_builder

    def _emit_progress(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    def _base_tags(
        self,
        group_id: str,
        scenario_id: str,
        role: str,
        extra: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
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

    def _requested_target_image(self, target_image: str | None) -> str:
        requested = str(target_image or self.config.default_target_image or "").strip()
        if requested:
            return requested
        if self.config.golden_ami_id:
            return "legacy-default"
        raise ValueError("No target image was requested and backend.default_target_image is not configured.")

    def _find_existing_golden_image(self, target_image: str) -> str | None:
        response = self.ec2.describe_images(
            Owners=["self"],
            Filters=[
                {"Name": "state", "Values": ["available"]},
                {"Name": "tag:EvalHarness", "Values": ["true"]},
                {"Name": "tag:EvalImageRole", "Values": ["golden"]},
                {"Name": "tag:EvalTargetImage", "Values": [target_image]},
            ],
        )
        images = list(response.get("Images", []))
        if not images:
            return None
        images.sort(key=lambda item: str(item.get("CreationDate", "")), reverse=True)
        return str(images[0]["ImageId"])

    def _build_missing_golden_image(self, target_image: str, target_config: AwsTargetImageConfig) -> ResolvedGoldenImage:
        if not self.config.openclaw_eval_token:
            raise RuntimeError("Controller token is required to build a golden AMI automatically.")
        self._emit_progress(f"Building golden AMI for target image {target_image} with Packer...")
        build_request = AwsPackerBuildRequest(
            target_image=target_image,
            aws_region=self.config.region,
            vpc_id=self.config.vpc_id,
            subnet_id=self.config.subnet_id,
            instance_type=self.config.instance_type,
            iam_instance_profile=self.config.instance_profile_name,
            openclaw_eval_token=self.config.openclaw_eval_token,
            packer_template_dir=target_config.packer_template_dir,
            distro_vars_file=target_config.distro_vars_file,
            node_major_version=target_config.node_major_version,
            openclaw_version=target_config.openclaw_version,
            packer_bin=target_config.packer_bin,
            timeout_seconds=self.config.golden_image_build_timeout_seconds,
        )
        result = self._golden_image_builder(build_request)
        image_id = str(result.image_id)
        self._emit_progress(f"Built golden AMI {image_id} for target image {target_image}.")
        return ResolvedGoldenImage(
            image_id=image_id,
            target_image=target_image,
            build_triggered=True,
            build_source="packer",
        )

    def _resolve_golden_image(self, target_image: str | None) -> ResolvedGoldenImage:
        requested_target = self._requested_target_image(target_image)
        if (
            self.config.golden_ami_id
            and requested_target in {"legacy-default", self.config.default_target_image}
        ):
            return ResolvedGoldenImage(
                image_id=self.config.golden_ami_id,
                target_image=requested_target,
                build_triggered=False,
                build_source="legacy_config",
            )

        target_config = self.config.target_images.get(requested_target)
        if target_config is None:
            available = ", ".join(sorted(self.config.target_images))
            raise ValueError(
                f"Unsupported target image {requested_target!r}. "
                f"Configured target images: {available or 'none'}."
            )

        existing_image_id = self._find_existing_golden_image(requested_target)
        if existing_image_id:
            return ResolvedGoldenImage(
                image_id=existing_image_id,
                target_image=requested_target,
                build_triggered=False,
                build_source="existing_ami",
            )
        return self._build_missing_golden_image(requested_target, target_config)

    def launch_staging(self, group_id: str, scenario_id: str, *, target_image: str | None = None) -> SandboxHandle:
        resolved = self._resolve_golden_image(target_image)
        name = f"{self.config.staging_name_prefix}-{group_id}"
        tags = self._base_tags(
            group_id,
            scenario_id,
            "staging",
            {
                "Name": name,
                "EvalTargetImage": resolved.target_image,
            },
        )
        handle = self._launch_instance(image_id=resolved.image_id, name=name, tags=tags)
        metadata = dict(handle.metadata)
        metadata.update(
            {
                "requested_target_image": self._requested_target_image(target_image),
                "resolved_target_image": resolved.target_image,
                "resolved_golden_ami_id": resolved.image_id,
                "golden_image_build_triggered": resolved.build_triggered,
                "golden_image_build_source": resolved.build_source,
            }
        )
        return SandboxHandle(
            handle_id=handle.handle_id,
            kind=handle.kind,
            backend_name=handle.backend_name,
            remote_id=handle.remote_id,
            image_id=handle.image_id,
            local_port=handle.local_port,
            metadata=metadata,
        )

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
