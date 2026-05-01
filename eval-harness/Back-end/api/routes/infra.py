"""AWS infrastructure routes.

Thin pass-through routes that reuse eval_harness.aws_auth.resolve_aws_profile
and existing boto3 client patterns from eval_harness.backends.aws.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from ..schemas import ImageItem, InstanceItem, PreflightResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["infra"])


def _get_ec2_client():
    """Build a boto3 EC2 client using the harness AWS profile."""
    from eval_harness.aws_auth import preflight_aws, resolve_aws_profile

    import boto3

    preflight_aws()
    profile, region = resolve_aws_profile()
    return boto3.Session(profile_name=profile, region_name=region).client("ec2")


def _parse_tags(tag_list: list[dict[str, str]] | None) -> dict[str, str]:
    """Convert boto3 tag list [{Key:..., Value:...}, ...] to plain dict."""
    if not tag_list:
        return {}
    return {
        item["Key"]: item.get("Value", "")
        for item in tag_list
        if isinstance(item, dict)
    }


def _parse_launched_at(instance: dict) -> datetime | None:
    raw = instance.get("LaunchTime")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None) if raw.tzinfo else raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except (ValueError, TypeError):
        return None


@router.get("/infra/instances")
def list_instances() -> list[InstanceItem]:
    """Describe EC2 instances tagged with EvalHarness=true."""
    try:
        ec2 = _get_ec2_client()
        response = ec2.describe_instances(
            Filters=[
                {"Name": "tag:EvalHarness", "Values": ["true"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ],
        )
    except Exception as exc:
        logger.exception("Failed to describe instances")
        raise HTTPException(
            status_code=500, detail=f"AWS describe_instances failed: {exc}"
        ) from exc

    instances: list[InstanceItem] = []
    for res in response.get("Reservations", []):
        for inst in res.get("Instances", []):
            public_ip = inst.get("PublicIpAddress")
            instances.append(
                InstanceItem(
                    instance_id=inst["InstanceId"],
                    state=inst.get("State", {}).get("Name", "unknown"),
                    instance_type=inst.get("InstanceType", "unknown"),
                    public_ip=public_ip,
                    tags=_parse_tags(inst.get("Tags")),
                    launched_at=_parse_launched_at(inst),
                )
            )
    return instances


@router.get("/infra/images")
def list_images() -> list[ImageItem]:
    """Describe AMIs tagged with EvalHarness=true, owned by this account."""
    try:
        ec2 = _get_ec2_client()
        response = ec2.describe_images(
            Owners=["self"],
            Filters=[
                {"Name": "state", "Values": ["available"]},
                {"Name": "tag:EvalHarness", "Values": ["true"]},
            ],
        )
    except Exception as exc:
        logger.exception("Failed to describe images")
        raise HTTPException(
            status_code=500, detail=f"AWS describe_images failed: {exc}"
        ) from exc

    images: list[ImageItem] = []
    for img in response.get("Images", []):
        creation_date = img.get("CreationDate")
        created_at: datetime | None = None
        if creation_date:
            with contextlib.suppress(ValueError, TypeError):
                created_at = datetime.fromisoformat(
                    str(creation_date).replace("Z", "+00:00")
                ).replace(tzinfo=None)
        images.append(
            ImageItem(
                image_id=img["ImageId"],
                name=img.get("Name"),
                state=img.get("State", "unknown"),
                tags=_parse_tags(img.get("Tags")),
                created_at=created_at,
            )
        )
    return images


@router.post("/infra/preflight")
def run_preflight() -> PreflightResponse:
    """Run AWS credential + tool preflight checks."""
    from eval_harness.aws_auth import AwsPreflightError, preflight_aws

    try:
        preflight_aws()
        return PreflightResponse(ok=True, message="AWS preflight passed.")
    except AwsPreflightError as exc:
        return PreflightResponse(ok=False, message=str(exc))


@router.delete("/infra/instances/{instance_id}")
def terminate_instance(instance_id: str, confirm: int = Query(default=0)):
    """Terminate an EC2 instance. Requires ?confirm=1 for safety."""
    if confirm != 1:
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=1 to terminate the instance.",
        )
    try:
        ec2 = _get_ec2_client()
        ec2.terminate_instances(InstanceIds=[instance_id])
    except Exception as exc:
        logger.exception("Failed to terminate instance %s", instance_id)
        raise HTTPException(
            status_code=500,
            detail=f"terminate_instances({instance_id}) failed: {exc}",
        ) from exc
    return {"ok": True, "instance_id": instance_id}


@router.delete("/infra/images/{image_id}")
def deregister_image(image_id: str):
    """Deregister an AMI. Also deletes associated snapshots."""
    try:
        ec2 = _get_ec2_client()
        # Gather snapshots before deregistering
        images_resp = ec2.describe_images(ImageIds=[image_id])
        images = images_resp.get("Images", [])
        snapshot_ids: list[str] = []
        if images:
            for mapping in images[0].get("BlockDeviceMappings", []):
                ebs = mapping.get("Ebs") or {}
                sid = ebs.get("SnapshotId")
                if sid:
                    snapshot_ids.append(str(sid))

        ec2.deregister_image(ImageId=image_id)

        for sid in snapshot_ids:
            try:
                ec2.delete_snapshot(SnapshotId=sid)
            except Exception:
                logger.warning(
                    "Failed to delete snapshot %s for image %s", sid, image_id
                )

    except Exception as exc:
        logger.exception("Failed to deregister image %s", image_id)
        raise HTTPException(
            status_code=500,
            detail=f"deregister_image({image_id}) failed: {exc}",
        ) from exc
    return {"ok": True, "image_id": image_id}
