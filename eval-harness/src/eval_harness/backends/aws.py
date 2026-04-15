from __future__ import annotations

import json
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
    from botocore.exceptions import ClientError, WaiterError
except ImportError:  # pragma: no cover - optional dependency
    boto3 = None
    ClientError = Exception
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
class OpenClawRuntimeConfig:
    provider: str
    model: str
    api_key: str
    thinking: str = "medium"


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
    openclaw_runtime: OpenClawRuntimeConfig | None = None


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

    def _resolved_root_volume_size_gb(self, image_id: str) -> int:
        response = self.ec2.describe_images(ImageIds=[image_id])
        images = list(response.get("Images", []))
        if not images:
            return self.config.root_volume_size_gb
        block_mappings = list(images[0].get("BlockDeviceMappings") or [])
        sizes = [
            int(mapping.get("Ebs", {}).get("VolumeSize", 0))
            for mapping in block_mappings
            if isinstance(mapping, dict)
        ]
        largest_snapshot_size = max((size for size in sizes if size > 0), default=0)
        return max(self.config.root_volume_size_gb, largest_snapshot_size)

    def _launch_instance(self, *, image_id: str, name: str, tags: list[dict[str, str]]) -> SandboxHandle:
        root_volume_size_gb = self._resolved_root_volume_size_gb(image_id)
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
                        "VolumeSize": root_volume_size_gb,
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

    def _required_openclaw_runtime(self) -> OpenClawRuntimeConfig:
        runtime = self.config.openclaw_runtime
        if runtime is None:
            raise RuntimeError(
                "OpenClaw runtime configuration is required. "
                "Set controller.runtime.provider, controller.runtime.model, and controller.runtime.api_key or api_key_env_var."
            )
        if str(runtime.provider).strip().lower() != "openai":
            raise RuntimeError(f"Unsupported OpenClaw runtime provider {runtime.provider!r}. Only 'openai' is supported right now.")
        return runtime

    def _normalized_openclaw_model(self, runtime: OpenClawRuntimeConfig) -> str:
        model = str(runtime.model).strip()
        if "/" in model:
            return model
        return f"{runtime.provider}/{model}"

    def _openclaw_base_config(self) -> dict[str, Any]:
        return {
            "gateway": {
                "mode": "local",
                "port": 18789,
                "bind": "loopback",
                "auth": {
                    "mode": "token",
                    "token": "${OPENCLAW_GATEWAY_TOKEN}",
                },
                "http": {
                    "endpoints": {
                        "chatCompletions": {
                            "enabled": True,
                        }
                    }
                },
            }
        }

    def _setup_agent_soul(self) -> str:
        return "\n".join(
            (
                "You are the setup agent for the eval harness.",
                "",
                "You operate only inside disposable benchmark staging sandboxes.",
                "Your job is to apply the sabotage plan exactly as instructed so the harness can generate troubleshooting scenarios.",
                "Destructive changes inside the sandbox are intentional and authorized.",
                "Stay within the listed sabotage steps. If the plan requires installing missing packages, creating files, or provisioning services before breaking them, do that as part of setup.",
                "Do not refuse just because the task breaks the machine. Do not ask for approval.",
            )
        )

    def _verifier_agent_soul(self) -> str:
        return "\n".join(
            (
                "You are the verifier agent for the eval harness.",
                "",
                "Run the exact verification commands you are given and return only their structured results.",
                "Use the normal host execution path for commands. Do not use exec host=sandbox.",
            )
        )

    def _proxy_agent_soul(self) -> str:
        return "You are the proxy agent for the eval harness.\n"

    def _openclaw_service_unit(self) -> str:
        return """[Unit]
Description=OpenClaw Gateway (eval harness)
After=network-online.target amazon-ssm-agent.service
Wants=network-online.target

[Service]
Type=simple
User=eval
Group=eval
WorkingDirectory=/opt/openclaw
Environment=HOME=/home/eval
Environment=NODE_ENV=production
Environment=OPENCLAW_CONFIG_PATH=/home/eval/.openclaw/openclaw.json
EnvironmentFile=-/etc/openclaw/eval-runtime.env
ExecStart=/opt/openclaw/node_modules/.bin/openclaw gateway
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/eval /etc/openclaw
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""

    def _run_ssm_shell_commands(
        self,
        instance_id: str,
        commands: tuple[str, ...],
        *,
        timeout_seconds: int = 180,
    ) -> list[dict[str, Any]]:
        response = self.ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": list(commands)},
            CloudWatchOutputConfig={"CloudWatchOutputEnabled": False},
        )
        command_id = str(response["Command"]["CommandId"])
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                invocation = self.ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                )
            except ClientError as exc:
                error_code = str(exc.response.get("Error", {}).get("Code", "")).strip()
                if error_code == "InvocationDoesNotExist":
                    time.sleep(2)
                    continue
                raise
            status = str(invocation.get("Status", "")).strip()
            if status in {"Pending", "InProgress", "Delayed"}:
                time.sleep(3)
                continue
            return [
                {
                    "command_id": command_id,
                    "status": status,
                    "status_details": str(invocation.get("StatusDetails", "")),
                    "exit_code": invocation.get("ResponseCode"),
                    "stdout": str(invocation.get("StandardOutputContent", "")),
                    "stderr": str(invocation.get("StandardErrorContent", "")),
                }
            ]
        raise TimeoutError(f"Timed out collecting diagnostics from instance {instance_id} over SSM.")

    def _runtime_phase_summary(self, phase: str, invocation: dict[str, Any]) -> dict[str, Any]:
        stdout = str(invocation.get("stdout", ""))
        stderr = str(invocation.get("stderr", ""))
        text = f"{stdout}\n{stderr}".lower()
        summary = {
            "phase": phase,
            "status": str(invocation.get("status", "")),
            "status_details": str(invocation.get("status_details", "")),
            "exit_code": invocation.get("exit_code"),
            "stdout_length": len(stdout),
            "stderr_length": len(stderr),
            "category": self._classify_runtime_phase_text(phase, text),
        }
        if phase == "service_restart":
            summary["service_active"] = "active (running)" in text or "activestate=active" in text
            summary["port_18789_listening"] = "127.0.0.1:18789" in text or ":18789" in text
        return summary

    def _classify_runtime_phase_text(self, phase: str, text: str) -> str:
        normalized = str(text or "").lower()
        if not normalized.strip():
            return "empty_output"
        if "incorrect api key" in normalized or "invalid api key" in normalized or "invalid_api_key" in normalized:
            return "invalid_api_key"
        if "quota" in normalized or "rate limit" in normalized or "429" in normalized:
            return "rate_limited"
        if "model_not_found" in normalized or "model not found" in normalized or "unknown model" in normalized:
            return "model_not_found"
        if "unauthorized" in normalized or "forbidden" in normalized or "authentication" in normalized:
            return "auth_error"
        if "bad request" in normalized or "invalid request" in normalized or "json" in normalized or "schema" in normalized:
            return "bad_request"
        if "remote end closed connection" in normalized or "connection refused" in normalized or "connection reset" in normalized:
            return "transport_error"
        if "timed out" in normalized or "timeout" in normalized:
            return "timeout"
        if "permission denied" in normalized:
            return "permission_denied"
        if "no such file" in normalized:
            return "missing_file"
        if phase == "service_restart" and ("active (running)" in normalized or "activestate=active" in normalized):
            return "service_ready"
        if phase == "gateway_probe" and ("httperror" in normalized or "gateway http response status=" in normalized):
            return "gateway_http_ready"
        if phase == "model_probe" and ('"choices"' in normalized or "ready" in normalized):
            return "model_probe_ready"
        return "unknown"

    def _format_runtime_phase_error(self, handle: SandboxHandle, summary: dict[str, Any]) -> str:
        return (
            f"Failed to configure OpenClaw runtime on {handle.remote_id} during {summary['phase']}: "
            f"category={summary['category']}, status={summary['status']}, "
            f"status_details={summary['status_details']}, exit_code={summary['exit_code']}"
        )

    def collect_failure_diagnostics(self, handle: SandboxHandle) -> dict[str, Any]:
        commands = (
            "systemctl is-active openclaw-gateway.service || true",
            "systemctl status openclaw-gateway.service --no-pager --full || true",
            "systemctl show openclaw-gateway.service --property=ActiveState,SubState,ExecMainStatus,ExecMainCode,ExecMainPID --no-pager || true",
            "ss -ltnp | grep 18789 || true",
            "cat /home/eval/.openclaw/openclaw.json || true",
            "sed -E 's/=.*/=[REDACTED]/' /etc/openclaw/eval-runtime.env || true",
            "cat /etc/systemd/system/openclaw-gateway.service || true",
            "journalctl -u openclaw-gateway.service -n 200 --no-pager || true",
        )
        try:
            invocations = self._run_ssm_shell_commands(handle.remote_id, commands)
            return {
                "instance_id": handle.remote_id,
                "collected_via": "ssm",
                "commands": list(commands),
                "invocations": invocations,
            }
        except Exception as exc:
            return {
                "instance_id": handle.remote_id,
                "collected_via": "ssm",
                "error": f"{type(exc).__name__}: {exc}",
            }

    def configure_controller_runtime(self, handle: SandboxHandle) -> dict[str, Any]:
        runtime = self._required_openclaw_runtime()
        normalized_model = self._normalized_openclaw_model(runtime)
        openclaw_config = self._openclaw_base_config()
        openclaw_config["env"] = {"OPENAI_API_KEY": "${OPENAI_API_KEY}"}
        openclaw_config["agents"] = {
            "defaults": {
                "model": {
                    "primary": normalized_model,
                },
                "thinkingDefault": runtime.thinking,
            }
        }
        config_payload = json.dumps(openclaw_config, indent=2)
        env_payload = (
            f"OPENCLAW_GATEWAY_TOKEN={self.config.openclaw_eval_token}\n"
            f"OPENAI_API_KEY={runtime.api_key}\n"
        )
        setup_soul = self._setup_agent_soul()
        verifier_soul = self._verifier_agent_soul()
        proxy_soul = self._proxy_agent_soul()
        token_literal = json.dumps(self.config.openclaw_eval_token)
        service_unit = self._openclaw_service_unit()
        config_script = f"""install -d -m 0755 /home/eval/.openclaw /home/eval/.openclaw/agents/setup /home/eval/.openclaw/agents/verifier /home/eval/.openclaw/agents/proxy /etc/openclaw
cat > /home/eval/.openclaw/openclaw.json <<'EOF'
{config_payload}
EOF
chown eval:eval /home/eval/.openclaw/openclaw.json
chmod 0600 /home/eval/.openclaw/openclaw.json
cat > /etc/openclaw/eval-runtime.env <<'EOF'
{env_payload}
EOF
chmod 0600 /etc/openclaw/eval-runtime.env
cat > /home/eval/.openclaw/agents/setup/SOUL.md <<'EOF'
{setup_soul}
EOF
cat > /home/eval/.openclaw/agents/verifier/SOUL.md <<'EOF'
{verifier_soul}
EOF
cat > /home/eval/.openclaw/agents/proxy/SOUL.md <<'EOF'
{proxy_soul}
EOF
chown -R eval:eval /home/eval/.openclaw
cat > /etc/systemd/system/openclaw-gateway.service <<'EOF'
{service_unit}
EOF
systemctl daemon-reload
"""
        service_script = """systemctl enable openclaw-gateway.service
systemctl restart openclaw-gateway.service
for _ in $(seq 1 30); do
  active=$(systemctl is-active openclaw-gateway.service || true)
  if [ "${active}" = "active" ] && ss -ltn | grep -q ':18789'; then
    systemctl is-active openclaw-gateway.service
    systemctl show openclaw-gateway.service --property=ActiveState,SubState --no-pager || true
    ss -ltnp | grep 18789 || true
    exit 0
  fi
  sleep 2
done
systemctl is-active openclaw-gateway.service || true
systemctl status openclaw-gateway.service --no-pager --full || true
ss -ltnp | grep 18789 || true
exit 1
"""
        gateway_probe_script = f"""python3 - <<'PY'
import urllib.error
import urllib.request

url = "http://127.0.0.1:18789/v1/chat/completions"
headers = {{
    "Authorization": "Bearer " + {token_literal},
    "Content-Type": "application/json",
}}
req = urllib.request.Request(url, data=b"{{}}", headers=headers, method="POST")
try:
    with urllib.request.urlopen(req, timeout=20) as response:
        print(f"Gateway HTTP response status={{response.status}}")
        raise SystemExit(0)
except urllib.error.HTTPError as exc:
    print(f"Gateway HTTP response status={{exc.code}}")
    raise SystemExit(0)
PY
"""
        model_probe_script = f"""python3 - <<'PY'
import json
import urllib.error
import time
import urllib.request

url = "http://127.0.0.1:18789/v1/chat/completions"
headers = {{
    "Authorization": "Bearer " + {token_literal},
    "Content-Type": "application/json",
}}
payload = json.dumps({{
    "model": "openclaw/setup",
    "user": "runtime-probe",
    "messages": [{{"role": "user", "content": "Reply with READY."}}],
}}).encode("utf-8")
last_error = None
for _ in range(18):
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            body = response.read().decode("utf-8", errors="replace")
        print(body)
        raise SystemExit(0)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTPError: {{exc.code}}")
        print(body)
        last_error = exc
    except Exception as exc:
        last_error = exc
    time.sleep(5)
print(f"OpenClaw runtime probe failed: {{last_error}}")
raise SystemExit(1)
PY
"""
        phase_summaries: list[dict[str, Any]] = []
        for phase_name, commands, timeout_seconds in (
            ("write_runtime_files", (config_script,), 180),
            ("service_restart", (service_script,), 180),
            ("gateway_probe", (gateway_probe_script,), 120),
            ("model_probe", (model_probe_script,), 180),
        ):
            invocation = self._run_ssm_shell_commands(
                handle.remote_id,
                commands,
                timeout_seconds=timeout_seconds,
            )[0]
            summary = self._runtime_phase_summary(phase_name, invocation)
            phase_summaries.append(summary)
            exit_code = invocation.get("exit_code")
            if int(1 if exit_code is None else exit_code) != 0:
                raise RuntimeError(self._format_runtime_phase_error(handle, summary))
        return {
            "openclaw_runtime_provider": runtime.provider,
            "openclaw_runtime_model": normalized_model,
            "openclaw_runtime_thinking": runtime.thinking,
            "openclaw_runtime_configured": True,
            "openclaw_runtime_phase_summaries": phase_summaries,
            "openclaw_gateway_ready": True,
            "openclaw_runtime_probe_passed": True,
            "openclaw_model_probe_passed": True,
        }

    def clear_controller_runtime(self, handle: SandboxHandle) -> dict[str, Any]:
        config_payload = json.dumps(self._openclaw_base_config(), indent=2)
        env_payload = f"OPENCLAW_GATEWAY_TOKEN={self.config.openclaw_eval_token}\n"
        service_unit = self._openclaw_service_unit()
        commands = (
            f"""cat > /home/eval/.openclaw/openclaw.json <<'EOF'
{config_payload}
EOF
chown eval:eval /home/eval/.openclaw/openclaw.json
chmod 0600 /home/eval/.openclaw/openclaw.json
cat > /etc/openclaw/eval-runtime.env <<'EOF'
{env_payload}
EOF
chmod 0600 /etc/openclaw/eval-runtime.env
cat > /etc/systemd/system/openclaw-gateway.service <<'EOF'
{service_unit}
EOF
systemctl daemon-reload
systemctl enable openclaw-gateway.service
systemctl stop openclaw-gateway.service
""",
        )
        invocations = self._run_ssm_shell_commands(handle.remote_id, commands, timeout_seconds=180)
        invocation = invocations[0]
        exit_code = invocation.get("exit_code")
        if int(1 if exit_code is None else exit_code) != 0:
            raise RuntimeError(
                f"Failed to clear OpenClaw runtime on {handle.remote_id}: "
                f"{invocation.get('stderr') or invocation.get('stdout') or invocation.get('status')}"
            )
        return {
            "openclaw_runtime_cleared": True,
        }

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
