from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .adapters.ai_linux_assistant_http import AILinuxAssistantHttpAdapter, AILinuxAssistantHttpConfig
from .artifacts import ArtifactStore, PostgresArtifactExporter
from .backends.aws import AwsEc2Backend, AwsEc2BackendConfig
from .controllers.openclaw import OpenClawControllerFactory, OpenClawControllerFactoryConfig
from .judges.openai_compatible import OpenAICompatibleBlindJudge, OpenAICompatibleBlindJudgeConfig
from .orchestration import BenchmarkRunOrchestrator, JudgeJobOrchestrator, ScenarioSetupOrchestrator
from .persistence import EvalHarnessStore, build_engine, build_session_factory, create_all_tables
from .planners.openai_compatible import OpenAICompatibleScenarioPlanner, OpenAICompatibleScenarioPlannerConfig
from .scenario import load_scenario, validate_scenario
from .models import PlannerScenarioRequest


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


def _load_resolved_config(path: str | Path) -> dict[str, Any]:
    return _resolve_env_placeholders(_load_json(path))


def _store_from_config(config: dict[str, Any]) -> EvalHarnessStore:
    database_config = dict(config.get("database", {}) or {})
    engine = build_engine(database_config.get("url"))
    return EvalHarnessStore(build_session_factory(engine))


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
        clone_name_prefix=str(config.get("clone_name_prefix", "eval-clone")),
        default_tags={str(key): str(value) for key, value in dict(config.get("default_tags", {}) or {}).items()},
        root_volume_size_gb=int(config.get("root_volume_size_gb", 16)),
        image_wait_timeout_seconds=int(config.get("image_wait_timeout_seconds", 1800)),
        ssm_wait_timeout_seconds=int(config.get("ssm_wait_timeout_seconds", 600)),
        terminate_wait_seconds=int(config.get("terminate_wait_seconds", 300)),
        use_spot=bool(config.get("use_spot", False)),
    )
    return AwsEc2Backend(backend_config)


def _controller_factory_from_config(config: dict[str, Any]) -> OpenClawControllerFactory:
    if config.get("type", "openclaw") != "openclaw":
        raise ValueError(f"Unsupported controller type {config.get('type')!r}.")
    factory_config = OpenClawControllerFactoryConfig(
        token=str(config["token"]),
        default_session_key_prefix=str(config.get("default_session_key_prefix", "eval-harness")),
        request_timeout_seconds=int(config.get("request_timeout_seconds", 60)),
        fixed_base_url=(str(config["fixed_base_url"]).strip() if config.get("fixed_base_url") else None),
        aws_region=(str(config["aws_region"]).strip() if config.get("aws_region") else None),
        remote_port=int(config.get("remote_port", 3000)),
    )
    return OpenClawControllerFactory(factory_config)


def _planner_from_config(config: dict[str, Any]) -> OpenAICompatibleScenarioPlanner:
    if config.get("type", "openai_compatible") != "openai_compatible":
        raise ValueError(f"Unsupported planner type {config.get('type')!r}.")
    planner_config = OpenAICompatibleScenarioPlannerConfig(
        base_url=str(config["base_url"]),
        model=str(config["model"]),
        api_key=str(config["api_key"]),
        request_timeout_seconds=float(config.get("request_timeout_seconds", 60.0)),
    )
    return OpenAICompatibleScenarioPlanner(planner_config)


def _judge_from_config(config: dict[str, Any]) -> OpenAICompatibleBlindJudge:
    if config.get("type", "openai_compatible") != "openai_compatible":
        raise ValueError(f"Unsupported judge type {config.get('type')!r}.")
    judge_config = OpenAICompatibleBlindJudgeConfig(
        base_url=str(config["base_url"]),
        model=str(config["model"]),
        api_key=str(config["api_key"]),
        request_timeout_seconds=float(config.get("request_timeout_seconds", 60.0)),
    )
    return OpenAICompatibleBlindJudge(judge_config)


def _subject_adapters_from_config(config: dict[str, Any]) -> dict[str, AILinuxAssistantHttpAdapter]:
    adapters: dict[str, AILinuxAssistantHttpAdapter] = {}
    adapter_configs = dict(config.get("subject_adapters", {}) or {})
    if not adapter_configs and config.get("adapter"):
        adapter_configs = {"ai_linux_assistant_http": dict(config.get("adapter", {}) or {})}
    for adapter_type, adapter_config in adapter_configs.items():
        adapter_payload = dict(adapter_config or {})
        resolved_type = str(adapter_payload.get("type", adapter_type))
        if resolved_type != "ai_linux_assistant_http":
            raise ValueError(f"Unsupported subject adapter type {resolved_type!r}.")
        adapters[adapter_type] = AILinuxAssistantHttpAdapter(
            AILinuxAssistantHttpConfig(
                base_url=str(adapter_payload["base_url"]),
                request_timeout_seconds=float(adapter_payload.get("request_timeout_seconds", 30.0)),
                poll_interval_seconds=float(adapter_payload.get("poll_interval_seconds", 1.0)),
                poll_timeout_seconds=float(adapter_payload.get("poll_timeout_seconds", 1800.0)),
                project_name_prefix=str(adapter_payload.get("project_name_prefix", "eval-harness")),
                default_bearer_token=adapter_payload.get("default_bearer_token"),
                bearer_tokens_by_subject={
                    str(key): str(value)
                    for key, value in dict(adapter_payload.get("bearer_tokens_by_subject", {}) or {}).items()
                },
                legacy_bootstrap_usernames_by_subject={
                    str(key): str(value)
                    for key, value in dict(adapter_payload.get("legacy_bootstrap_usernames_by_subject", {}) or {}).items()
                },
            )
        )
    return adapters


def _sync_subjects(store: EvalHarnessStore, subjects_payload: list[dict[str, Any]]) -> None:
    for subject in subjects_payload:
        store.upsert_subject(
            subject_name=str(subject["subject_name"]),
            adapter_type=str(subject["adapter_type"]),
            display_name=str(subject.get("display_name", subject["subject_name"])),
            adapter_config=dict(subject.get("adapter_config", {}) or {}),
            is_active=bool(subject.get("is_active", True)),
        )


def _command_init_db(args: argparse.Namespace) -> int:
    config = _load_resolved_config(args.config)
    database_config = dict(config.get("database", {}) or {})
    engine = build_engine(database_config.get("url"))
    create_all_tables(engine)
    print(json.dumps({"database_url": database_config.get("url", "env"), "status": "initialized"}, indent=2))
    return 0


def _command_generate_scenario(args: argparse.Namespace) -> int:
    config = _load_resolved_config(args.config)
    planner = _planner_from_config(dict(config.get("planner", {}) or {}))
    request = PlannerScenarioRequest.from_dict(_load_json(args.request))
    scenario = planner.generate_scenario(request)
    validate_scenario(scenario)
    output_payload = scenario.to_dict()
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
    print(json.dumps(output_payload, indent=2))
    return 0


def _command_verify_scenario(args: argparse.Namespace) -> int:
    config = _load_resolved_config(args.config)
    backend = _aws_backend_from_config(dict(config.get("backend", {}) or {}))
    controller_factory = _controller_factory_from_config(dict(config.get("controller", {}) or {}))
    planner = _planner_from_config(dict(config.get("planner", {}) or {}))
    store = _store_from_config(config)
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=controller_factory,
        planner=planner,
        store=store,
    )
    request = PlannerScenarioRequest.from_dict(_load_json(args.request))
    result = orchestrator.run(
        request,
        group_id=args.group_id,
        sabotage_agent_id=args.sabotage_agent_id,
        verification_agent_id=args.verification_agent_id,
        max_corrections=args.max_corrections,
    )
    print(json.dumps(result.__dict__, indent=2))
    return 0


def _command_run_benchmark(args: argparse.Namespace) -> int:
    config = _load_resolved_config(args.config)
    backend = _aws_backend_from_config(dict(config.get("backend", {}) or {}))
    controller_factory = _controller_factory_from_config(dict(config.get("controller", {}) or {}))
    store = _store_from_config(config)
    _sync_subjects(store, list(config.get("subjects", []) or []))
    subject_adapters = _subject_adapters_from_config(config)
    setup_run = store.get_setup_run(args.setup_run_id)
    if setup_run is None:
        raise ValueError(f"Unknown setup run {args.setup_run_id}")
    orchestrator = BenchmarkRunOrchestrator(
        backend=backend,
        controller_factory=controller_factory,
        subject_adapters=subject_adapters,
        store=store,
    )
    result = orchestrator.run(
        scenario_revision_id=setup_run.scenario_revision_id,
        verified_setup_run_id=args.setup_run_id,
        user_proxy_agent_id=args.user_proxy_agent_id,
        verification_agent_id=args.verification_agent_id,
    )
    print(json.dumps(result.__dict__, indent=2))
    return 0


def _command_run_judge_job(args: argparse.Namespace) -> int:
    config = _load_resolved_config(args.config)
    store = _store_from_config(config)
    judge = _judge_from_config(dict(config.get("judge", {}) or {}))
    orchestrator = JudgeJobOrchestrator(judge=judge, store=store)
    result = orchestrator.run(benchmark_run_id=args.benchmark_run_id)
    print(json.dumps(result.__dict__, indent=2))
    return 0


def _command_export_artifact_pack(args: argparse.Namespace) -> int:
    config = _load_resolved_config(args.config)
    store = _store_from_config(config)
    exporter = PostgresArtifactExporter(store)
    pack = exporter.export_benchmark_run(
        args.benchmark_run_id,
        backend_name=str(dict(config.get("backend", {}) or {}).get("type", "aws_ec2")),
        controller_name=str(dict(config.get("controller", {}) or {}).get("type", "openclaw")),
    )
    artifact_store = ArtifactStore(args.artifacts_root)
    path = artifact_store.save_pack(pack)
    print(json.dumps({"artifact_path": str(path), "benchmark_run_id": args.benchmark_run_id}, indent=2))
    return 0


def _command_validate_scenario(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    validate_scenario(scenario)
    print(json.dumps(scenario.to_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eval-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db_parser = subparsers.add_parser("init-db", help="Create eval-harness Postgres tables.")
    init_db_parser.add_argument("--config", required=True)
    init_db_parser.set_defaults(func=_command_init_db)

    generate_parser = subparsers.add_parser("generate-scenario", help="Use the planner to generate a scenario draft.")
    generate_parser.add_argument("--config", required=True)
    generate_parser.add_argument("--request", required=True)
    generate_parser.add_argument("--output")
    generate_parser.set_defaults(func=_command_generate_scenario)

    verify_parser = subparsers.add_parser("verify-scenario", help="Generate and verify a scenario on staging.")
    verify_parser.add_argument("--config", required=True)
    verify_parser.add_argument("--request", required=True)
    verify_parser.add_argument("--group-id", required=True)
    verify_parser.add_argument("--sabotage-agent-id", default="sabotage_agent")
    verify_parser.add_argument("--verification-agent-id", default="verification_executor")
    verify_parser.add_argument("--max-corrections", type=int, default=2)
    verify_parser.set_defaults(func=_command_verify_scenario)

    benchmark_parser = subparsers.add_parser("run-benchmark", help="Run all active subjects against a verified setup run.")
    benchmark_parser.add_argument("--config", required=True)
    benchmark_parser.add_argument("--setup-run-id", required=True)
    benchmark_parser.add_argument("--user-proxy-agent-id", default="user_proxy_agent")
    benchmark_parser.add_argument("--verification-agent-id", default="verification_executor")
    benchmark_parser.set_defaults(func=_command_run_benchmark)

    judge_parser = subparsers.add_parser("run-judge-job", help="Blind-grade a completed benchmark run.")
    judge_parser.add_argument("--config", required=True)
    judge_parser.add_argument("--benchmark-run-id", required=True)
    judge_parser.set_defaults(func=_command_run_judge_job)

    export_parser = subparsers.add_parser("export-artifact-pack", help="Export a Postgres-backed benchmark run to JSON artifacts.")
    export_parser.add_argument("--config", required=True)
    export_parser.add_argument("--benchmark-run-id", required=True)
    export_parser.add_argument("--artifacts-root", default="artifacts")
    export_parser.set_defaults(func=_command_export_artifact_pack)

    validate_parser = subparsers.add_parser("validate-scenario", help="Validate and print a scenario JSON file.")
    validate_parser.add_argument("scenario")
    validate_parser.set_defaults(func=_command_validate_scenario)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
