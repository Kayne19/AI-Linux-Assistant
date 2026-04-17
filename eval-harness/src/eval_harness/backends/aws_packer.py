from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class AwsPackerBuildRequest:
    target_image: str
    aws_region: str
    subnet_id: str
    iam_instance_profile: str
    packer_template_dir: Path
    distro_vars_file: Path
    instance_type: str = "t3.small"
    vpc_id: str = ""
    packer_bin: str = "packer"
    timeout_seconds: int = 3600


@dataclass(frozen=True)
class AwsPackerBuildResult:
    image_id: str


def render_build_vars(request: AwsPackerBuildRequest, manifest_path: Path) -> str:
    values = {
        "aws_region": request.aws_region,
        "vpc_id": request.vpc_id,
        "subnet_id": request.subnet_id,
        "instance_type": request.instance_type,
        "iam_instance_profile": request.iam_instance_profile,
        "manifest_output": str(manifest_path),
    }
    return "\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items()) + "\n"


def parse_manifest_ami_id(manifest_path: Path) -> str:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    builds = payload.get("builds") or []
    if not builds:
        raise RuntimeError(f"Packer manifest {manifest_path} did not contain any builds.")
    artifact_id = str(builds[-1].get("artifact_id", "")).strip()
    if not artifact_id or ":" not in artifact_id:
        raise RuntimeError(f"Could not parse an AMI id from packer manifest {manifest_path}.")
    image_id = artifact_id.rsplit(":", 1)[-1].strip()
    if not image_id.startswith("ami-"):
        raise RuntimeError(f"Packer manifest {manifest_path} did not contain a valid AMI id.")
    return image_id


def build_packer_commands(request: AwsPackerBuildRequest, temp_vars_path: Path) -> tuple[list[str], list[str]]:
    init_command = [request.packer_bin, "init", "."]
    build_command = [
        request.packer_bin,
        "build",
        f"-var-file={temp_vars_path}",
        f"-var-file={request.distro_vars_file}",
        ".",
    ]
    return init_command, build_command


def _load_exported_aws_credentials() -> dict[str, str]:
    if shutil.which("aws") is None:
        return {}
    command = ["aws", "configure", "export-credentials", "--format", "process"]
    profile = str(os.environ.get("AWS_PROFILE", "")).strip()
    if profile:
        command.extend(["--profile", profile])
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    env_updates = {
        "AWS_ACCESS_KEY_ID": str(payload.get("AccessKeyId", "")).strip(),
        "AWS_SECRET_ACCESS_KEY": str(payload.get("SecretAccessKey", "")).strip(),
        "AWS_SESSION_TOKEN": str(payload.get("SessionToken", "")).strip(),
    }
    region = str(os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "").strip()
    if profile:
        env_updates["AWS_PROFILE"] = profile
    if region:
        env_updates["AWS_REGION"] = region
        env_updates["AWS_DEFAULT_REGION"] = region
    return {key: value for key, value in env_updates.items() if value}


def _stream_subprocess(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    output_stream: TextIO,
    env: dict[str, str] | None = None,
) -> None:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            print(line.rstrip("\n"), file=output_stream, flush=True)
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait(timeout=5)
        raise TimeoutError(f"Timed out running {' '.join(command)}.") from exc
    if return_code != 0:
        raise RuntimeError(f"Packer command failed with exit code {return_code}: {' '.join(command)}")


def build_golden_ami(
    request: AwsPackerBuildRequest,
    *,
    output_stream: TextIO | None = None,
) -> AwsPackerBuildResult:
    stream = output_stream or sys.stderr
    subprocess_env = os.environ.copy()
    subprocess_env.update(_load_exported_aws_credentials())
    with tempfile.TemporaryDirectory(prefix="eval-harness-packer-") as temp_dir:
        temp_path = Path(temp_dir)
        manifest_path = temp_path / "packer-manifest.json"
        temp_vars_path = temp_path / "generated.auto.pkrvars.hcl"
        temp_vars_path.write_text(
            render_build_vars(request, manifest_path),
            encoding="utf-8",
        )

        init_command, build_command = build_packer_commands(request, temp_vars_path)
        _stream_subprocess(
            init_command,
            cwd=request.packer_template_dir,
            timeout_seconds=request.timeout_seconds,
            output_stream=stream,
            env=subprocess_env,
        )
        _stream_subprocess(
            build_command,
            cwd=request.packer_template_dir,
            timeout_seconds=request.timeout_seconds,
            output_stream=stream,
            env=subprocess_env,
        )

        image_id = parse_manifest_ami_id(manifest_path)
        return AwsPackerBuildResult(image_id=image_id)
