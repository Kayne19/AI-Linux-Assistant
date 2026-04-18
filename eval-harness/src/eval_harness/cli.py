from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .adapters.ai_linux_assistant_http import AILinuxAssistantHttpAdapter, AILinuxAssistantHttpConfig
from .artifacts import ArtifactStore, PostgresArtifactExporter
from .backends.aws import AwsEc2Backend, AwsEc2BackendConfig, AwsTargetImageConfig
from .controllers.ssm import SsmControllerFactory, SsmControllerFactoryConfig
from .judges.anthropic import AnthropicBlindJudge, AnthropicBlindJudgeConfig
from .judges.google_genai import GoogleGenAIBlindJudge, GoogleGenAIBlindJudgeConfig
from .judges.openai_responses import OpenAIResponsesBlindJudge, OpenAIResponsesBlindJudgeConfig
from .orchestration import BenchmarkRunOrchestrator, JudgeJobOrchestrator, ScenarioSetupOrchestrator
from .orchestration.progress import stderr_progress_sink
from .orchestration.user_proxy_llm import UserProxyLLMClient, UserProxyLLMClientConfig
from .orchestration.user_proxy_llm_anthropic import AnthropicUserProxyLLMClient
from .orchestration.user_proxy_llm_google import GoogleGenAIUserProxyLLMClient
from .persistence import EvalHarnessStore, build_engine, build_session_factory, create_all_tables
from .planners.anthropic import AnthropicScenarioPlanner, AnthropicScenarioPlannerConfig
from .planners.google_genai import GoogleGenAIScenarioPlanner, GoogleGenAIScenarioPlannerConfig
from .planners.openai_responses import OpenAIResponsesScenarioPlanner, OpenAIResponsesScenarioPlannerConfig
from .scenario import load_scenario, validate_scenario
from .models import PlannerScenarioRequest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


def _autoload_dotenv(path: Path = DEFAULT_ENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


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


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean value {value!r}; expected one of: true/false, yes/no, on/off, 1/0")
    return bool(value)


def _load_resolved_config(path: str | Path) -> dict[str, Any]:
    return _resolve_env_placeholders(_load_json(path))


def _resolve_project_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _redact_database_url(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    username = parsed.username or ""
    password = parsed.password
    if parsed.username is None and parsed.password is None:
        return raw
    userinfo = username
    if password is not None:
        userinfo = f"{userinfo}:***"
    netloc = f"{userinfo}@{hostname}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _store_from_config(config: dict[str, Any]) -> EvalHarnessStore:
    database_config = dict(config.get("database", {}) or {})
    engine = build_engine(database_config.get("url"))
    return EvalHarnessStore(build_session_factory(engine))


def _aws_backend_from_config(config: dict[str, Any], *, controller_config: dict[str, Any] | None = None) -> AwsEc2Backend:
    if config.get("type", "aws_ec2") != "aws_ec2":
        raise ValueError(f"Unsupported backend type {config.get('type')!r}.")
    target_images: dict[str, AwsTargetImageConfig] = {}
    for target_image, payload in dict(config.get("target_images", {}) or {}).items():
        item = dict(payload or {})
        distro_vars_file = str(item.get("distro_vars_file", "")).strip()
        if not distro_vars_file:
            raise ValueError(f"backend.target_images[{target_image!r}] is missing distro_vars_file.")
        target_images[target_image] = AwsTargetImageConfig(
            target_image=target_image,
            packer_template_dir=_resolve_project_path(str(item.get("packer_template_dir", "infra/aws/packer"))),
            distro_vars_file=_resolve_project_path(distro_vars_file),
            packer_bin=str(item.get("packer_bin", "packer")),
        )
    backend_config = AwsEc2BackendConfig(
        region=str(config["region"]),
        subnet_id=str(config["subnet_id"]),
        security_group_ids=tuple(str(item) for item in config["security_group_ids"]),
        instance_profile_name=str(config["instance_profile_name"]),
        instance_type=str(config.get("instance_type", "t3.small")),
        staging_name_prefix=str(config.get("staging_name_prefix", "eval-staging")),
        clone_name_prefix=str(config.get("clone_name_prefix", "eval-clone")),
        default_tags={str(key): str(value) for key, value in dict(config.get("default_tags", {}) or {}).items()},
        root_volume_size_gb=int(config.get("root_volume_size_gb", 16)),
        image_wait_timeout_seconds=int(config.get("image_wait_timeout_seconds", 1800)),
        ssm_wait_timeout_seconds=int(config.get("ssm_wait_timeout_seconds", 600)),
        terminate_wait_seconds=int(config.get("terminate_wait_seconds", 300)),
        use_spot=bool(config.get("use_spot", False)),
        vpc_id=str(config.get("vpc_id", "")),
        default_target_image=str(config.get("default_target_image", "")).strip(),
        target_images=target_images,
        golden_ami_id=(str(config["golden_ami_id"]).strip() if config.get("golden_ami_id") else None),
        golden_image_build_timeout_seconds=int(config.get("golden_image_build_timeout_seconds", 3600)),
    )
    return AwsEc2Backend(backend_config)


def _controller_factory_from_config(config: dict[str, Any]) -> SsmControllerFactory:
    controller_type = str(config.get("type") or config.get("kind") or "ssm").strip()
    if controller_type != "ssm":
        raise ValueError(f"Unsupported controller type {controller_type!r}. Only 'ssm' is supported.")
    ssm_config = SsmControllerFactoryConfig(
        default_session_key_prefix=str(config.get("default_session_key_prefix", "eval-harness")),
        aws_region=str(config["aws_region"]),
        command_timeout_seconds=int(config.get("command_timeout_seconds", 600)),
    )
    return SsmControllerFactory(ssm_config)


def _provider_from_role_config(config: dict[str, Any], *, default_provider: str = "openai") -> str:
    legacy_type = str(config.get("type") or "").strip()
    if legacy_type == "openai_compatible":
        raise ValueError(f"Unsupported legacy type {legacy_type!r}.")
    provider = str(config.get("provider") or "").strip().lower()
    if provider:
        return provider
    if legacy_type in {"", "openai_responses"}:
        return default_provider
    raise ValueError(f"Unsupported provider/type {legacy_type!r}.")


def _planner_from_config(config: dict[str, Any]):
    provider = _provider_from_role_config(config)
    base_url = str(config["base_url"]).strip() if config.get("base_url") is not None else None
    raw_timeout = config.get("request_timeout_seconds")
    common_kwargs = {
        "model": str(config["model"]),
        "api_key": str(config["api_key"]),
        "base_url": base_url,
        "request_timeout_seconds": float(raw_timeout) if raw_timeout is not None else None,
        "max_output_tokens": int(config["max_output_tokens"]) if config.get("max_output_tokens") is not None else None,
        "reasoning_effort": str(config["reasoning_effort"]) if config.get("reasoning_effort") is not None else None,
    }
    if provider == "openai":
        try:
            web_search_enabled = _coerce_bool(config.get("web_search_enabled"), default=True)
        except ValueError as exc:
            raise ValueError(f"Invalid planner.web_search_enabled value: {exc}") from exc
        return OpenAIResponsesScenarioPlanner(
            OpenAIResponsesScenarioPlannerConfig(
                **common_kwargs,
                web_search_enabled=web_search_enabled,
            )
        )
    if provider == "anthropic":
        return AnthropicScenarioPlanner(AnthropicScenarioPlannerConfig(**common_kwargs))
    if provider == "google":
        return GoogleGenAIScenarioPlanner(GoogleGenAIScenarioPlannerConfig(**common_kwargs))
    raise ValueError(f"Unsupported planner provider {provider!r}.")


def _user_proxy_llm_from_config(config: dict[str, Any]) -> Any:
    provider = _provider_from_role_config(config)
    raw_max_tokens = config.get("max_output_tokens")
    raw_reasoning = config.get("reasoning_effort")
    client_config = UserProxyLLMClientConfig(
        base_url=(str(config["base_url"]).strip() if config.get("base_url") is not None else None),
        model=str(config["model"]),
        api_key=str(config["api_key"]),
        request_timeout_seconds=float(config.get("request_timeout_seconds", 60.0)),
        max_output_tokens=int(raw_max_tokens) if raw_max_tokens is not None else None,
        reasoning_effort=str(raw_reasoning) if raw_reasoning is not None else None,
    )
    if provider == "openai":
        return UserProxyLLMClient(client_config)
    if provider == "anthropic":
        return AnthropicUserProxyLLMClient(client_config)
    if provider == "google":
        return GoogleGenAIUserProxyLLMClient(client_config)
    raise ValueError(f"Unsupported user_proxy_llm provider {provider!r}.")


def _judge_from_config(config: dict[str, Any]):
    provider = _provider_from_role_config(config)
    base_url = str(config["base_url"]).strip() if config.get("base_url") is not None else None
    common_kwargs = {
        "model": str(config["model"]),
        "api_key": str(config["api_key"]),
        "base_url": base_url,
        "request_timeout_seconds": float(config.get("request_timeout_seconds", 60.0)),
        "max_output_tokens": int(config["max_output_tokens"]) if config.get("max_output_tokens") is not None else None,
        "reasoning_effort": str(config["reasoning_effort"]) if config.get("reasoning_effort") is not None else None,
    }
    if provider == "openai":
        return OpenAIResponsesBlindJudge(OpenAIResponsesBlindJudgeConfig(**common_kwargs))
    if provider == "anthropic":
        return AnthropicBlindJudge(AnthropicBlindJudgeConfig(**common_kwargs))
    if provider == "google":
        return GoogleGenAIBlindJudge(GoogleGenAIBlindJudgeConfig(**common_kwargs))
    raise ValueError(f"Unsupported judge provider {provider!r}.")


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
    print(json.dumps({"database_url": _redact_database_url(database_config.get("url", "env")), "status": "initialized"}, indent=2))
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
    controller_config = dict(config.get("controller", {}) or {})
    backend = _aws_backend_from_config(dict(config.get("backend", {}) or {}), controller_config=controller_config)
    controller_factory = _controller_factory_from_config(controller_config)
    planner = _planner_from_config(dict(config.get("planner", {}) or {}))
    store = _store_from_config(config)
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=controller_factory,
        planner=planner,
        store=store,
        progress=stderr_progress_sink(),
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
    controller_config = dict(config.get("controller", {}) or {})
    backend = _aws_backend_from_config(dict(config.get("backend", {}) or {}), controller_config=controller_config)
    controller_factory = _controller_factory_from_config(controller_config)
    store = _store_from_config(config)
    _sync_subjects(store, list(config.get("subjects", []) or []))
    subject_adapters = _subject_adapters_from_config(config)
    setup_run = store.get_setup_run(args.setup_run_id)
    if setup_run is None:
        raise ValueError(f"Unknown setup run {args.setup_run_id}")
    user_proxy_llm_config = dict(config.get("user_proxy_llm", {}) or {})
    if not user_proxy_llm_config:
        raise ValueError("Config is missing required 'user_proxy_llm' section.")
    orchestrator = BenchmarkRunOrchestrator(
        backend=backend,
        controller_factory=controller_factory,
        subject_adapters=subject_adapters,
        store=store,
        user_proxy_llm=_user_proxy_llm_from_config(user_proxy_llm_config),
        user_proxy_mode=str(user_proxy_llm_config.get("mode", "pragmatic_human")),
        progress=stderr_progress_sink(),
    )
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _interrupt_benchmark(signum, frame):  # type: ignore[no-untyped-def]
        del frame
        raise KeyboardInterrupt(f"Benchmark interrupted by signal {signum}")

    signal.signal(signal.SIGINT, _interrupt_benchmark)
    signal.signal(signal.SIGTERM, _interrupt_benchmark)
    try:
        result = orchestrator.run(
            scenario_revision_id=setup_run.scenario_revision_id,
            verified_setup_run_id=args.setup_run_id,
            user_proxy_agent_id=args.user_proxy_agent_id,
            verification_agent_id=args.verification_agent_id,
        )
        print(json.dumps(result.__dict__, indent=2))
        return 0
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


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
        controller_name=str(dict(config.get("controller", {}) or {}).get("type", "ssm")),
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
    verify_parser.add_argument("--sabotage-agent-id", default="setup")
    verify_parser.add_argument("--verification-agent-id", default="verifier")
    verify_parser.add_argument("--max-corrections", type=int, default=2)
    verify_parser.set_defaults(func=_command_verify_scenario)

    benchmark_parser = subparsers.add_parser("run-benchmark", help="Run all active subjects against a verified setup run.")
    benchmark_parser.add_argument("--config", required=True)
    benchmark_parser.add_argument("--setup-run-id", required=True)
    benchmark_parser.add_argument("--user-proxy-agent-id", default="proxy")
    benchmark_parser.add_argument("--verification-agent-id", default="verifier")
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
    _autoload_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
