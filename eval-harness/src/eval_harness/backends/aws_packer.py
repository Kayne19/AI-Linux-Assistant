from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class AwsPackerBuildRequest:
    target_image: str
    aws_region: str
    subnet_id: str
    iam_instance_profile: str
    openclaw_eval_token: str
    packer_template_dir: Path
    distro_vars_file: Path
    instance_type: str = "t3.small"
    node_major_version: str = "24"
    openclaw_version: str = "2026.4.11"
    vpc_id: str = ""
    packer_bin: str = "packer"
    timeout_seconds: int = 3600


@dataclass(frozen=True)
class AwsPackerBuildResult:
    image_id: str


def openclaw_tarball_url(version: str) -> str:
    return f"https://registry.npmjs.org/openclaw/-/openclaw-{version}.tgz"


def render_build_vars(request: AwsPackerBuildRequest, manifest_path: Path, openclaw_bundle_path: Path) -> str:
    values = {
        "aws_region": request.aws_region,
        "vpc_id": request.vpc_id,
        "subnet_id": request.subnet_id,
        "instance_type": request.instance_type,
        "iam_instance_profile": request.iam_instance_profile,
        "openclaw_bundle_path": str(openclaw_bundle_path),
        "openclaw_eval_token": request.openclaw_eval_token,
        "node_major_version": request.node_major_version,
        "openclaw_version": request.openclaw_version,
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


def prepare_openclaw_bundle(
    request: AwsPackerBuildRequest,
    bundle_archive_path: Path,
    *,
    output_stream: TextIO,
) -> Path:
    npm_bin = shutil.which("npm")
    if not npm_bin:
        raise RuntimeError("npm is required to prepare the local OpenClaw bundle before running Packer.")

    bundle_root = bundle_archive_path.parent / "openclaw-bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)
    print(
        f"Preparing pinned OpenClaw bundle {request.openclaw_version} locally before the AMI bake...",
        file=output_stream,
        flush=True,
    )
    install_env = os.environ.copy()
    install_env.update(
        {
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_loglevel": "warn",
            "OPENCLAW_DISABLE_BUNDLED_PLUGIN_POSTINSTALL": "1",
        }
    )
    install_command = [
        npm_bin,
        "install",
        "--omit=dev",
        "--no-package-lock",
        f"--prefix={bundle_root}",
        openclaw_tarball_url(request.openclaw_version),
    ]
    _stream_subprocess(
        install_command,
        cwd=bundle_root,
        timeout_seconds=request.timeout_seconds,
        output_stream=output_stream,
        env=install_env,
    )

    openclaw_bin = bundle_root / "node_modules" / ".bin" / "openclaw"
    if not openclaw_bin.exists():
        raise RuntimeError(
            f"Local OpenClaw bundle preparation did not produce {openclaw_bin}."
        )
    _stream_subprocess(
        [str(openclaw_bin), "--version"],
        cwd=bundle_root,
        timeout_seconds=min(120, request.timeout_seconds),
        output_stream=output_stream,
        env=install_env,
    )

    with tarfile.open(bundle_archive_path, "w:gz") as archive:
        for item in sorted(bundle_root.iterdir(), key=lambda path: path.name):
            archive.add(item, arcname=item.name)
    print(f"Prepared OpenClaw bundle archive at {bundle_archive_path}.", file=output_stream, flush=True)
    return bundle_archive_path


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
        openclaw_bundle_path = temp_path / "openclaw-bundle.tgz"
        temp_vars_path = temp_path / "generated.auto.pkrvars.hcl"
        prepare_openclaw_bundle(request, openclaw_bundle_path, output_stream=stream)
        temp_vars_path.write_text(
            render_build_vars(request, manifest_path, openclaw_bundle_path),
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
