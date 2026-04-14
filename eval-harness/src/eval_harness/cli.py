from __future__ import annotations

import argparse
import json
import os
from itertools import count
from pathlib import Path
from typing import Any

from .adapters.ai_linux_assistant_http import AILinuxAssistantHttpAdapter, AILinuxAssistantHttpConfig
from .artifacts import ArtifactStore
from .backends.aws import AwsEc2Backend, AwsEc2BackendConfig
from .controllers.openclaw import OpenClawController, OpenClawControllerConfig
from .orchestrator import EvalOrchestrator
from .plugins.example_grader import ExampleArtifactGrader
from .runtime.ssm import SsmPortForwardSession
from .scenario import load_scenario, validate_scenario


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return payload


def _resolve_env_placeholders(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("env:"):
        env_name = value.split(":", 1)[1].strip()
        if not env_name:
            raise ValueError("Empty environment variable placeholder.")
        resolved = os.getenv(env_name)
        if resolved is None:
            raise ValueError(f"Environment variable {env_name} is not set.")
        return resolved
    if isinstance(value, dict):
        return {key: _resolve_env_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(item) for item in value]
    return value


def _aws_backend_from_config(config: dict[str, Any]) -> AwsEc2Backend:
    if config.get("type", "aws_ec2") != "aws_ec2":
        raise ValueError(f"Unsupported backend type {config.get('type')!r}.")
    backend_config = AwsEc2BackendConfig(
        region=str(config["region"]),
        subnet_id=str(config["subnet_id"]),
        security_group_ids=tuple(str(item) for item in config["security_group_ids"]),
        instance_profile_name=str(config["instance_profile_name"]),
        golden_ami_id=str(config["golden_ami_id"]),
        instance_type=str(config.get("instance_type", "t3.small")),
        staging_name_prefix=str(config.get("staging_name_prefix", "eval-staging")),
        variant_name_prefix=str(config.get("variant_name_prefix", "eval-variant")),
        default_tags={str(key): str(value) for key, value in dict(config.get("default_tags", {}) or {}).items()},
        root_volume_size_gb=int(config.get("root_volume_size_gb", 16)),
        image_wait_timeout_seconds=int(config.get("image_wait_timeout_seconds", 1800)),
        ssm_wait_timeout_seconds=int(config.get("ssm_wait_timeout_seconds", 600)),
        terminate_wait_seconds=int(config.get("terminate_wait_seconds", 300)),
        use_spot=bool(config.get("use_spot", False)),
    )
    return AwsEc2Backend(backend_config)


def _openclaw_controller_factory(
    *,
    backend: AwsEc2Backend,
    config: dict[str, Any],
    local_port_start: int,
):
    if config.get("type", "openclaw") != "openclaw":
        raise ValueError(f"Unsupported controller type {config.get('type')!r}.")
    remote_port = int(config.get("remote_port", 8080))
    request_timeout_seconds = int(config.get("request_timeout_seconds", 60))
    token = str(config["token"])
    port_counter = count(local_port_start)

    def factory(handle, session_name: str):
        local_port = next(port_counter)
        port_forward = SsmPortForwardSession(
            instance_id=handle.remote_id,
            local_port=local_port,
            remote_port=remote_port,
            region=backend.config.region,
        )
        port_forward.start()
        controller_config = OpenClawControllerConfig(
            base_url=f"http://127.0.0.1:{local_port}",
            token=token,
            default_session_key=session_name,
            request_timeout_seconds=request_timeout_seconds,
        )
        return OpenClawController(controller_config, port_forward_session=port_forward)

    return factory


def _adapter_from_config(config: dict[str, Any]) -> AILinuxAssistantHttpAdapter:
    if config.get("type", "ai_linux_assistant_http") != "ai_linux_assistant_http":
        raise ValueError(f"Unsupported adapter type {config.get('type')!r}.")
    adapter_config = AILinuxAssistantHttpConfig(
        base_url=str(config["base_url"]),
        request_timeout_seconds=float(config.get("request_timeout_seconds", 30.0)),
        poll_interval_seconds=float(config.get("poll_interval_seconds", 1.0)),
        poll_timeout_seconds=float(config.get("poll_timeout_seconds", 1800.0)),
        project_name_prefix=str(config.get("project_name_prefix", "eval-harness")),
        default_bearer_token=config.get("default_bearer_token"),
        bearer_tokens_by_variant={
            str(key): str(value)
            for key, value in dict(config.get("bearer_tokens_by_variant", {}) or {}).items()
        },
        legacy_bootstrap_usernames_by_variant={
            str(key): str(value)
            for key, value in dict(config.get("legacy_bootstrap_usernames_by_variant", {}) or {}).items()
        },
    )
    return AILinuxAssistantHttpAdapter(adapter_config)


def _command_validate_scenario(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    validate_scenario(scenario)
    print(json.dumps(scenario.to_dict(), indent=2))
    return 0


def _command_grade_artifact(args: argparse.Namespace) -> int:
    artifact_store = ArtifactStore(args.artifacts_root)
    pack = artifact_store.load_pack(args.artifact)
    grader = ExampleArtifactGrader()
    output = grader.grade(pack)
    path = artifact_store.save_plugin_output(pack.group_id, output)
    print(json.dumps({"plugin_output": str(path), "metrics": output.metrics}, indent=2))
    return 0


def _command_run_group(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    config = _resolve_env_placeholders(_load_json(args.config))
    if args.dry_run:
        print(
            json.dumps(
                {
                    "scenario_id": scenario.scenario_id,
                    "group_id": args.group_id,
                    "backend_type": config.get("backend", {}).get("type", "aws_ec2"),
                    "controller_type": config.get("controller", {}).get("type", "openclaw"),
                    "adapter_type": config.get("adapter", {}).get("type", "ai_linux_assistant_http"),
                    "variant_names": [variant.name for variant in scenario.variants],
                },
                indent=2,
            )
        )
        return 0

    backend = _aws_backend_from_config(dict(config.get("backend", {}) or {}))
    controller_factory = _openclaw_controller_factory(
        backend=backend,
        config=dict(config.get("controller", {}) or {}),
        local_port_start=int(args.local_port_start),
    )
    adapter = _adapter_from_config(dict(config.get("adapter", {}) or {}))
    artifact_store = ArtifactStore(args.artifacts_root)
    orchestrator = EvalOrchestrator(
        backend=backend,
        controller_factory=controller_factory,
        adapter=adapter,
        artifact_store=artifact_store,
    )
    pack = orchestrator.run_group(scenario, group_id=args.group_id)
    print(json.dumps({"group_id": pack.group_id, "artifact_path": str(artifact_store.pack_path(pack.group_id))}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eval-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-scenario", help="Validate and print a scenario spec.")
    validate_parser.add_argument("scenario")
    validate_parser.set_defaults(func=_command_validate_scenario)

    grade_parser = subparsers.add_parser("grade-artifact", help="Run the example grader on a stored artifact pack.")
    grade_parser.add_argument("artifact")
    grade_parser.add_argument("--artifacts-root", default="artifacts")
    grade_parser.set_defaults(func=_command_grade_artifact)

    run_parser = subparsers.add_parser("run-group", help="Execute one scenario group.")
    run_parser.add_argument("scenario")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--artifacts-root", default="artifacts")
    run_parser.add_argument("--group-id", required=True)
    run_parser.add_argument("--local-port-start", type=int, default=9100)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(func=_command_run_group)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
