from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .adapters.ai_linux_assistant_http import AILinuxAssistantHttpAdapter, AILinuxAssistantHttpConfig
from .adapters.openai_chatgpt import OpenAIChatGPTAdapter, OpenAIChatGPTConfig
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
from .adapters.base import SubjectAdapter
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


_DEFAULT_JUDGE_PAIRWISE = {
    "provider": "openai",
    "model": "gpt-5.4-nano",
    "api_key": "env:EVAL_HARNESS_JUDGE_API_KEY",
    "request_timeout_seconds": 90,
}

_DEFAULT_JUDGE_ABSOLUTE = {
    "provider": "openai",
    "model": "gpt-5.4-nano",
    "api_key": "env:EVAL_HARNESS_JUDGE_API_KEY",
    "request_timeout_seconds": 120,
}


def _judges_from_config(config: dict[str, Any], *, mode: str = "absolute") -> tuple[list, list[float]]:
    """Return (judges_list, weights_list) from config.

    Accepts a top-level ``judges:`` list or falls back to the legacy ``judge:`` block.
    If neither is present, applies a sensible default based on ``mode``.
    """
    judges_cfg: list[dict[str, Any]] | None = config.get("judges")
    if not judges_cfg:
        single = dict(config.get("judge", {}) or {})
        if single:
            judges_cfg = [single]
        else:
            # Apply default per mode.
            judges_cfg = [_DEFAULT_JUDGE_PAIRWISE if mode == "pairwise" else _DEFAULT_JUDGE_ABSOLUTE]

    # Resolve env: placeholders inside each entry.
    judges_cfg = [_resolve_env_placeholders(entry) for entry in judges_cfg]

    judges = []
    weights = []
    for entry in judges_cfg:
        entry = dict(entry)
        weight = float(entry.pop("weight", 1.0))
        judges.append(_judge_from_config(entry))
        weights.append(weight)
    return judges, weights


def _subject_adapters_from_config(config: dict[str, Any]) -> dict[str, SubjectAdapter]:
    adapters: dict[str, SubjectAdapter] = {}
    adapter_configs = dict(config.get("subject_adapters", {}) or {})
    if not adapter_configs and config.get("adapter"):
        adapter_configs = {"ai_linux_assistant_http": dict(config.get("adapter", {}) or {})}
    for adapter_type, adapter_config in adapter_configs.items():
        adapter_payload = dict(adapter_config or {})
        resolved_type = str(adapter_payload.get("type", adapter_type))
        if resolved_type == "ai_linux_assistant_http":
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
            continue
        if resolved_type == "openai_chatgpt":
            if "model" not in adapter_payload:
                raise ValueError("subject_adapters.openai_chatgpt is missing model.")
            if "api_key" not in adapter_payload:
                raise ValueError("subject_adapters.openai_chatgpt is missing api_key.")
            adapters[adapter_type] = OpenAIChatGPTAdapter(
                OpenAIChatGPTConfig(
                    model=str(adapter_payload["model"]),
                    api_key=str(adapter_payload["api_key"]),
                    base_url=(str(adapter_payload["base_url"]).strip() if adapter_payload.get("base_url") is not None else None),
                    request_timeout_seconds=(
                        float(adapter_payload["request_timeout_seconds"])
                        if adapter_payload.get("request_timeout_seconds") is not None
                        else None
                    ),
                    max_output_tokens=(
                        int(adapter_payload["max_output_tokens"]) if adapter_payload.get("max_output_tokens") is not None else None
                    ),
                    reasoning_effort=(
                        str(adapter_payload["reasoning_effort"]).strip()
                        if adapter_payload.get("reasoning_effort") is not None
                        else None
                    ),
                    instructions=str(adapter_payload.get("instructions", "") or ""),
                    conversation_state_mode=str(
                        adapter_payload.get("conversation_state_mode", "conversation")
                    ).strip()
                    or "conversation",
                    web_search_enabled=_coerce_bool(
                        adapter_payload.get("web_search_enabled"), default=True
                    ),
                    web_search_allowed_domains=tuple(
                        str(domain).strip()
                        for domain in (adapter_payload.get("web_search_allowed_domains") or [])
                        if str(domain).strip()
                    ),
                    web_search_user_location=(
                        dict(adapter_payload["web_search_user_location"])
                        if adapter_payload.get("web_search_user_location") is not None
                        else None
                    ),
                    web_search_include_sources=_coerce_bool(
                        adapter_payload.get("web_search_include_sources"), default=False
                    ),
                    web_search_search_context_size=(
                        str(adapter_payload["web_search_search_context_size"]).strip()
                        if adapter_payload.get("web_search_search_context_size") is not None
                        else None
                    ),
                    code_interpreter_enabled=_coerce_bool(
                        adapter_payload.get("code_interpreter_enabled"), default=True
                    ),
                    truncation=(
                        (str(adapter_payload["truncation"]).strip() or None)
                        if adapter_payload.get("truncation") is not None
                        else "auto"
                    ),
                )
            )
            continue
        raise ValueError(f"Unsupported subject adapter type {resolved_type!r}.")
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
    mode = getattr(args, "mode", None) or str(config.get("judge_default_mode", "absolute"))
    if mode not in {"absolute", "pairwise"}:
        raise ValueError(f"Unknown mode {mode!r}; expected 'absolute' or 'pairwise'.")
    anchor_subject = getattr(args, "anchor_subject", None)
    if anchor_subject is not None and mode != "pairwise":
        raise ValueError("--anchor-subject is only valid with --mode pairwise.")
    judges, weights = _judges_from_config(config, mode=mode)
    orchestrator = JudgeJobOrchestrator(judges=judges, store=store, weights=weights)
    if mode == "pairwise":
        result = orchestrator.run_pairwise(
            benchmark_run_id=args.benchmark_run_id,
            anchor_subject=anchor_subject,
        )
    else:
        result = orchestrator.run_absolute(benchmark_run_id=args.benchmark_run_id)
    print(json.dumps(result.__dict__, indent=2))
    return 0


def _command_calibrate_judge(args: argparse.Namespace) -> int:
    """Replay benchmark items through two judges and report agreement statistics."""
    import math
    import random

    config = _load_resolved_config(args.config)
    store = _store_from_config(config)
    mode = str(args.mode)
    rng = random.Random(args.rng_seed)

    # Build the two judges from config by name.
    judges_cfg_list = list(config.get("judges", []) or [])
    if not judges_cfg_list:
        single = dict(config.get("judge", {}) or {})
        if single:
            judges_cfg_list = [single]

    named_judges: dict[str, Any] = {}
    for entry in judges_cfg_list:
        entry = _resolve_env_placeholders(dict(entry))
        entry_copy = dict(entry)
        entry_copy.pop("weight", None)
        name = str(entry_copy.get("name", entry_copy.get("model", "unknown")))
        named_judges[name] = entry_copy

    def _get_judge(key: str):
        if key in named_judges:
            return _judge_from_config(named_judges[key])
        # Try treating key as a model id or provider:model.
        raise ValueError(
            f"Judge {key!r} not found in config. Available: {list(named_judges)}. "
            "Add it to the 'judges:' list in config."
        )

    strong_judge = _get_judge(args.strong)
    candidate_judge = _get_judge(args.candidate)

    # Fetch evaluation runs for the benchmark.
    benchmark_run = store.get_benchmark_run(args.benchmark_run_id)
    if benchmark_run is None:
        raise ValueError(f"Unknown benchmark run {args.benchmark_run_id}")
    revision = store.get_scenario_revision(benchmark_run.scenario_revision_id)
    if revision is None:
        raise ValueError(f"Unknown scenario revision {benchmark_run.scenario_revision_id}")

    from .judges.rubric import UNIVERSAL_RUBRIC, format_tagged_rubric
    from .mapping import turn_record_from_evaluation_event
    from .models import BlindJudgeRequest, PairwiseJudgeRequest

    scenario_items = tuple(
        str(item) for item in (revision.judge_rubric_json or {}).get("items", [])
    )
    rubric = format_tagged_rubric(UNIVERSAL_RUBRIC, scenario_items)

    eval_runs = list(store.list_evaluation_runs(args.benchmark_run_id))
    if not eval_runs:
        raise ValueError("No evaluation runs found for benchmark.")

    def _transcript(er):
        turns = []
        for event in store.list_evaluation_events(er.id):
            turn = turn_record_from_evaluation_event(event)
            if turn is not None:
                turns.append(turn.__class__(role=turn.role, content=turn.content, created_at=turn.created_at, metadata={}))
        return tuple(turns)

    max_pairs = int(args.max_pairs)

    if mode == "pairwise":
        from .models import PairwiseJudgeRequest

        # Build all pairs from eval_runs.
        import itertools
        all_pairs = list(itertools.combinations(eval_runs, 2))
        rng.shuffle(all_pairs)
        pairs = all_pairs[: max_pairs]

        strong_verdicts: list[str] = []
        candidate_verdicts: list[str] = []

        for er_a, er_b in pairs:
            t_a = _transcript(er_a)
            t_b = _transcript(er_b)
            req = PairwiseJudgeRequest(
                blind_label_a="A",
                blind_label_b="B",
                transcript_a=t_a,
                transcript_b=t_b,
                rubric=rubric,
                repair_success_a=er_a.repair_success,
                repair_success_b=er_b.repair_success,
            )
            s_res = strong_judge.compare(req)
            c_res = candidate_judge.compare(req)
            for sv, cv in zip(s_res.verdicts, c_res.verdicts):
                strong_verdicts.append(sv.winner)
                candidate_verdicts.append(cv.winner)

        # Compute Cohen's kappa and raw agreement on non-tie verdicts.
        categories = ["A", "B", "tie"]
        n = len(strong_verdicts)
        if n == 0:
            print(json.dumps({"error": "no verdicts to compare"}, indent=2))
            return 0

        agree = sum(1 for s, c in zip(strong_verdicts, candidate_verdicts) if s == c)
        raw_agreement = agree / n

        # Cohen's kappa.
        def _kappa(obs_a: list[str], obs_b: list[str]) -> float:
            n_ = len(obs_a)
            if n_ == 0:
                return 0.0
            p_o = sum(a == b for a, b in zip(obs_a, obs_b)) / n_
            freq_a = {c: obs_a.count(c) / n_ for c in categories}
            freq_b = {c: obs_b.count(c) / n_ for c in categories}
            p_e = sum(freq_a[c] * freq_b[c] for c in categories)
            if abs(1 - p_e) < 1e-9:
                return 1.0
            return (p_o - p_e) / (1 - p_e)

        kappa = _kappa(strong_verdicts, candidate_verdicts)

        # Non-tie agreement.
        non_tie_pairs = [(s, c) for s, c in zip(strong_verdicts, candidate_verdicts) if s != "tie" and c != "tie"]
        non_tie_agree = sum(1 for s, c in non_tie_pairs if s == c) / len(non_tie_pairs) if non_tie_pairs else None

        report = {
            "mode": "pairwise",
            "n_verdicts": n,
            "raw_agreement": round(raw_agreement, 4),
            "cohens_kappa": round(kappa, 4),
            "non_tie_agreement": round(non_tie_agree, 4) if non_tie_agree is not None else None,
            "strong": args.strong,
            "candidate": args.candidate,
        }

    else:
        # Absolute mode: MAE and Pearson correlation per criterion.
        sample = rng.sample(eval_runs, min(max_pairs, len(eval_runs)))
        criterion_strong: dict[str, list[float]] = {}
        criterion_candidate: dict[str, list[float]] = {}

        for er in sample:
            t = _transcript(er)
            req = BlindJudgeRequest(
                blind_label="subject",
                transcript=t,
                rubric=rubric,
                repair_success=er.repair_success,
            )
            s_res = strong_judge.grade(req)
            c_res = candidate_judge.grade(req)
            for crit in rubric:
                s_score = (s_res.scores.get(crit) or {}).get("score") if isinstance(s_res.scores.get(crit), dict) else s_res.scores.get(crit)
                c_score = (c_res.scores.get(crit) or {}).get("score") if isinstance(c_res.scores.get(crit), dict) else c_res.scores.get(crit)
                if s_score is not None and c_score is not None:
                    criterion_strong.setdefault(crit, []).append(float(s_score))
                    criterion_candidate.setdefault(crit, []).append(float(c_score))

        per_criterion: dict[str, dict] = {}
        for crit in criterion_strong:
            sv = criterion_strong[crit]
            cv = criterion_candidate.get(crit, [])
            if len(sv) != len(cv) or len(sv) == 0:
                continue
            mae = sum(abs(a - b) for a, b in zip(sv, cv)) / len(sv)
            mean_s = sum(sv) / len(sv)
            mean_c = sum(cv) / len(cv)
            num = sum((a - mean_s) * (b - mean_c) for a, b in zip(sv, cv))
            den_s = math.sqrt(sum((a - mean_s) ** 2 for a in sv))
            den_c = math.sqrt(sum((b - mean_c) ** 2 for b in cv))
            pearson = num / (den_s * den_c) if den_s > 0 and den_c > 0 else None
            per_criterion[crit] = {
                "mae": round(mae, 4),
                "pearson": round(pearson, 4) if pearson is not None else None,
                "n": len(sv),
            }

        report = {
            "mode": "absolute",
            "strong": args.strong,
            "candidate": args.candidate,
            "per_criterion": per_criterion,
        }

    print(json.dumps(report, indent=2))
    if getattr(args, "out", None):
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
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
    judge_parser.add_argument("--mode", choices=["absolute", "pairwise"], default=None,
                              help="Grading mode (default: from config judge_default_mode or 'absolute').")
    judge_parser.add_argument("--anchor-subject", default=None, metavar="NAME",
                              help="Only grade pairs including this subject (pairwise mode only).")
    judge_parser.add_argument("--bootstrap-samples", type=int, default=200, metavar="N",
                              help="Number of bootstrap samples for Bradley-Terry CIs (default 200).")
    judge_parser.add_argument("--rng-seed", type=int, default=None, metavar="N",
                              help="RNG seed for reproducible bootstrap sampling.")
    judge_parser.set_defaults(func=_command_run_judge_job)

    calibrate_parser = subparsers.add_parser(
        "calibrate-judge",
        help="Replay benchmark items through two judges and report agreement statistics.",
    )
    calibrate_parser.add_argument("--config", required=True)
    calibrate_parser.add_argument("--benchmark-run-id", required=True)
    calibrate_parser.add_argument("--strong", required=True, metavar="JUDGE_KEY",
                                  help="Name of the strong/reference judge from the 'judges:' config list.")
    calibrate_parser.add_argument("--candidate", required=True, metavar="JUDGE_KEY",
                                  help="Name of the candidate judge to calibrate against the strong judge.")
    calibrate_parser.add_argument("--max-pairs", type=int, default=50, metavar="N",
                                  help="Maximum number of pairs (pairwise) or items (absolute) to sample.")
    calibrate_parser.add_argument("--mode", choices=["absolute", "pairwise"], default="pairwise",
                                  help="Calibration mode (default: pairwise).")
    calibrate_parser.add_argument("--rng-seed", type=int, default=None, metavar="N",
                                  help="RNG seed for reproducible sampling.")
    calibrate_parser.add_argument("--out", default=None, metavar="PATH",
                                  help="Optional path to write the JSON report.")
    calibrate_parser.set_defaults(func=_command_calibrate_judge)

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
