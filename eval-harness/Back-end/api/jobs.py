"""Background-task dispatcher with per-scenario concurrency gate and orchestrator factories."""

from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import BackgroundTasks

JobFn = Callable[[], Awaitable[None]]

# ---------------------------------------------------------------------------
# Lightweight config loader (trimmed-down mirror of cli._load_resolved_config)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # eval-harness/


def _load_json(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_env_placeholders(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _resolve_env_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_placeholders(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("env:"):
        return os.environ.get(obj[4:].strip(), "")
    return obj


def _load_resolved_config(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        path = os.getenv("EVAL_HARNESS_CONFIG_PATH")
    if not path:
        path = PROJECT_ROOT / "examples" / "aws_ai_linux_assistant_config.json"
    return _resolve_env_placeholders(_load_json(path))


def _resolve_project_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


# ---------------------------------------------------------------------------
# Orchestrator factory helpers
# ---------------------------------------------------------------------------


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    if default is not False:
        return default
    raise ValueError(f"Cannot coerce {value!r} to bool.")


def _provider_from_role_config(
    config: dict[str, Any], *, default_provider: str = "openai"
) -> str:
    legacy_type = str(config.get("type") or "").strip()
    if legacy_type == "openai_compatible":
        raise ValueError(f"Unsupported legacy type {legacy_type!r}.")
    provider = str(config.get("provider") or "").strip().lower()
    if not provider:
        provider = str(default_provider)
    if provider not in ("openai", "anthropic", "google"):
        raise ValueError(f"Unsupported provider {provider!r}.")
    return provider


@lru_cache(maxsize=1)
def _get_store():
    from eval_harness.persistence import (
        EvalHarnessStore,
        build_engine,
        build_session_factory,
    )

    config = _load_resolved_config()
    db_cfg = dict(config.get("database", {}) or {})
    engine = build_engine(db_cfg.get("url"))
    return EvalHarnessStore(build_session_factory(engine))


def _build_backend_and_controller():
    from eval_harness.backends.aws import (
        AwsEc2Backend,
        AwsEc2BackendConfig,
        AwsTargetImageConfig,
    )
    from eval_harness.controllers.ssm import (
        SsmControllerFactory,
        SsmControllerFactoryConfig,
    )

    config = _load_resolved_config()
    backend_cfg = dict(config.get("backend", {}) or {})
    ctrl_cfg = dict(config.get("controller", {}) or {})

    target_images: dict[str, AwsTargetImageConfig] = {}
    for name, payload in dict(backend_cfg.get("target_images", {}) or {}).items():
        item = dict(payload or {})
        distro_vars_file = str(item.get("distro_vars_file", "")).strip()
        if not distro_vars_file:
            raise ValueError(f"Missing distro_vars_file for target_image {name!r}.")
        target_images[name] = AwsTargetImageConfig(
            target_image=name,
            packer_template_dir=_resolve_project_path(
                str(item.get("packer_template_dir", "infra/aws/packer"))
            ),
            distro_vars_file=_resolve_project_path(distro_vars_file),
            packer_bin=str(item.get("packer_bin", "packer")),
        )

    backend = AwsEc2Backend(
        AwsEc2BackendConfig(
            region=str(backend_cfg["region"]),
            subnet_id=str(backend_cfg["subnet_id"]),
            security_group_ids=tuple(str(s) for s in backend_cfg["security_group_ids"]),
            instance_profile_name=str(backend_cfg["instance_profile_name"]),
            instance_type=str(backend_cfg.get("instance_type", "t3.small")),
            staging_name_prefix=str(
                backend_cfg.get("staging_name_prefix", "eval-staging")
            ),
            clone_name_prefix=str(backend_cfg.get("clone_name_prefix", "eval-clone")),
            default_tags={
                str(k): str(v)
                for k, v in dict(backend_cfg.get("default_tags", {}) or {}).items()
            },
            root_volume_size_gb=int(backend_cfg.get("root_volume_size_gb", 16)),
            image_wait_timeout_seconds=int(
                backend_cfg.get("image_wait_timeout_seconds", 1800)
            ),
            ssm_wait_timeout_seconds=int(
                backend_cfg.get("ssm_wait_timeout_seconds", 600)
            ),
            terminate_wait_seconds=int(backend_cfg.get("terminate_wait_seconds", 300)),
            use_spot=bool(backend_cfg.get("use_spot", False)),
            vpc_id=str(backend_cfg.get("vpc_id", "")),
            default_target_image=str(
                backend_cfg.get("default_target_image", "")
            ).strip(),
            target_images=target_images,
            golden_ami_id=(
                str(backend_cfg["golden_ami_id"]).strip()
                if backend_cfg.get("golden_ami_id")
                else None
            ),
            golden_image_build_timeout_seconds=int(
                backend_cfg.get("golden_image_build_timeout_seconds", 3600)
            ),
        )
    )
    ctrl_type = str(ctrl_cfg.get("type") or ctrl_cfg.get("kind") or "ssm").strip()
    if ctrl_type != "ssm":
        raise ValueError(f"Unsupported controller type {ctrl_type!r}.")
    controller_factory = SsmControllerFactory(
        SsmControllerFactoryConfig(
            default_session_key_prefix=str(
                ctrl_cfg.get("default_session_key_prefix", "eval-harness")
            ),
            aws_region=str(ctrl_cfg["aws_region"]),
            command_timeout_seconds=int(ctrl_cfg.get("command_timeout_seconds", 600)),
        )
    )
    return backend, controller_factory


def _build_planner():
    from eval_harness.planners.anthropic import (
        AnthropicScenarioPlanner,
        AnthropicScenarioPlannerConfig,
    )
    from eval_harness.planners.google_genai import (
        GoogleGenAIScenarioPlanner,
        GoogleGenAIScenarioPlannerConfig,
    )
    from eval_harness.planners.openai_responses import (
        OpenAIResponsesScenarioPlanner,
        OpenAIResponsesScenarioPlannerConfig,
    )

    config = _load_resolved_config()
    planner_cfg = dict(config.get("planner", {}) or {})
    provider = _provider_from_role_config(planner_cfg)
    base_url = (
        str(planner_cfg["base_url"]).strip() if planner_cfg.get("base_url") else None
    )
    raw_timeout = planner_cfg.get("request_timeout_seconds")
    common_kwargs = {
        "model": str(planner_cfg["model"]),
        "api_key": str(planner_cfg["api_key"]),
        "base_url": base_url,
        "request_timeout_seconds": float(raw_timeout)
        if raw_timeout is not None
        else None,
        "max_output_tokens": int(planner_cfg["max_output_tokens"])
        if planner_cfg.get("max_output_tokens") is not None
        else None,
        "reasoning_effort": str(planner_cfg["reasoning_effort"])
        if planner_cfg.get("reasoning_effort") is not None
        else None,
    }
    if provider == "openai":
        web_search_enabled = _coerce_bool(
            planner_cfg.get("web_search_enabled"), default=True
        )
        return OpenAIResponsesScenarioPlanner(
            OpenAIResponsesScenarioPlannerConfig(
                **common_kwargs, web_search_enabled=web_search_enabled
            )
        )
    if provider == "anthropic":
        return AnthropicScenarioPlanner(AnthropicScenarioPlannerConfig(**common_kwargs))
    if provider == "google":
        return GoogleGenAIScenarioPlanner(
            GoogleGenAIScenarioPlannerConfig(**common_kwargs)
        )
    raise ValueError(f"Unsupported planner provider {provider!r}.")


def _build_subject_adapters() -> dict:
    from eval_harness.adapters.ai_linux_assistant_http import (
        AILinuxAssistantHttpAdapter,
        AILinuxAssistantHttpConfig,
    )
    from eval_harness.adapters.auth0_m2m import Auth0M2MConfig, ClientCreds
    from eval_harness.adapters.openai_chatgpt import (
        OpenAIChatGPTAdapter,
        OpenAIChatGPTConfig,
    )

    config = _load_resolved_config()
    adapters: dict = {}
    adapter_configs = dict(config.get("subject_adapters", {}) or {})
    if not adapter_configs and config.get("adapter"):
        adapter_configs = {
            "ai_linux_assistant_http": dict(config.get("adapter", {}) or {})
        }
    for adapter_type, adapter_cfg in adapter_configs.items():
        payload = dict(adapter_cfg or {})
        resolved_type = str(payload.get("type", adapter_type))
        if resolved_type == "ai_linux_assistant_http":
            raw_m2m = payload.get("auth0_m2m")
            if raw_m2m is None:
                raise ValueError(
                    "subject_adapters.ai_linux_assistant_http missing auth0_m2m block."
                )
            raw_m2m = _resolve_env_placeholders(dict(raw_m2m))
            raw_clients = dict(raw_m2m.get("clients_by_subject", {}) or {})
            clients_by_subject: dict[str, ClientCreds] = {}
            for subj, creds_raw in raw_clients.items():
                creds = _resolve_env_placeholders(dict(creds_raw or {}))
                clients_by_subject[str(subj)] = ClientCreds(
                    client_id=str(creds["client_id"]),
                    client_secret=str(creds["client_secret"]),
                )
            m2m_config = Auth0M2MConfig(
                token_url=str(raw_m2m["token_url"]),
                audience=str(raw_m2m["audience"]),
                clients_by_subject=clients_by_subject,
                refresh_skew_seconds=int(raw_m2m.get("refresh_skew_seconds", 60)),
                scope=str(raw_m2m["scope"]) if raw_m2m.get("scope") else None,
                organization=str(raw_m2m["organization"])
                if raw_m2m.get("organization")
                else None,
            )
            adapters[adapter_type] = AILinuxAssistantHttpAdapter(
                AILinuxAssistantHttpConfig(
                    base_url=str(payload["base_url"]),
                    auth0_m2m=m2m_config,
                    request_timeout_seconds=float(
                        payload.get("request_timeout_seconds", 30.0)
                    ),
                    poll_interval_seconds=float(
                        payload.get("poll_interval_seconds", 1.0)
                    ),
                    poll_timeout_seconds=float(
                        payload.get("poll_timeout_seconds", 1800.0)
                    ),
                    project_name_prefix=str(
                        payload.get("project_name_prefix", "eval-harness")
                    ),
                )
            )
        elif resolved_type == "openai_chatgpt":
            adapters[adapter_type] = OpenAIChatGPTAdapter(
                OpenAIChatGPTConfig(
                    model=str(payload["model"]),
                    api_key=str(payload["api_key"]),
                    request_timeout_seconds=float(
                        payload.get("request_timeout_seconds", 30.0)
                    ),
                )
            )
        else:
            raise ValueError(f"Unsupported subject_adapter type {resolved_type!r}.")
    return adapters


def _build_user_proxy_llm():
    from eval_harness.orchestration.user_proxy_llm import (
        UserProxyLLMClient,
        UserProxyLLMClientConfig,
    )
    from eval_harness.orchestration.user_proxy_llm_anthropic import (
        AnthropicUserProxyLLMClient,
    )
    from eval_harness.orchestration.user_proxy_llm_google import (
        GoogleGenAIUserProxyLLMClient,
    )

    config = _load_resolved_config()
    up_cfg = dict(config.get("user_proxy_llm", {}) or {})
    if not up_cfg:
        raise ValueError("Config missing user_proxy_llm section.")
    provider = _provider_from_role_config(up_cfg)
    raw_max_tokens = up_cfg.get("max_output_tokens")
    raw_reasoning = up_cfg.get("reasoning_effort")
    client_config = UserProxyLLMClientConfig(
        base_url=str(up_cfg["base_url"]).strip() if up_cfg.get("base_url") else None,
        model=str(up_cfg["model"]),
        api_key=str(up_cfg["api_key"]),
        request_timeout_seconds=float(up_cfg.get("request_timeout_seconds", 60.0)),
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


def _build_judges_and_weights(mode: str = "absolute"):
    from eval_harness.judges.anthropic import (
        AnthropicBlindJudge,
        AnthropicBlindJudgeConfig,
    )
    from eval_harness.judges.google_genai import (
        GoogleGenAIBlindJudge,
        GoogleGenAIBlindJudgeConfig,
    )
    from eval_harness.judges.openai_responses import (
        OpenAIResponsesBlindJudge,
        OpenAIResponsesBlindJudgeConfig,
    )

    config = _load_resolved_config()
    judges_cfg: list[dict] | None = config.get("judges")
    if not judges_cfg:
        single = dict(config.get("judge", {}) or {})
        if single:
            judges_cfg = [single]
        else:
            judges_cfg = [
                {
                    "provider": "openai",
                    "model": "gpt-5.4-nano",
                    "api_key": os.getenv("EVAL_HARNESS_JUDGE_API_KEY", ""),
                }
            ]
    judges: list = []
    weights: list[float] = []
    for entry in judges_cfg:
        entry = dict(entry)
        weight = float(entry.pop("weight", 1.0))
        provider = _provider_from_role_config(entry)
        base_url = str(entry["base_url"]).strip() if entry.get("base_url") else None
        common_kwargs = {
            "model": str(entry["model"]),
            "api_key": str(entry["api_key"]),
            "base_url": base_url,
            "request_timeout_seconds": float(
                entry.get("request_timeout_seconds", 60.0)
            ),
            "max_output_tokens": int(entry["max_output_tokens"])
            if entry.get("max_output_tokens") is not None
            else None,
            "reasoning_effort": str(entry["reasoning_effort"])
            if entry.get("reasoning_effort") is not None
            else None,
        }
        if provider == "openai":
            judges.append(
                OpenAIResponsesBlindJudge(
                    OpenAIResponsesBlindJudgeConfig(**common_kwargs)
                )
            )
        elif provider == "anthropic":
            judges.append(
                AnthropicBlindJudge(AnthropicBlindJudgeConfig(**common_kwargs))
            )
        elif provider == "google":
            judges.append(
                GoogleGenAIBlindJudge(GoogleGenAIBlindJudgeConfig(**common_kwargs))
            )
        else:
            raise ValueError(f"Unsupported judge provider {provider!r}.")
        weights.append(weight)
    return judges, weights


# ---------------------------------------------------------------------------
# Progress sink for BackgroundTasks (logs to stderr)
# ---------------------------------------------------------------------------


def _job_progress_sink():
    from eval_harness.orchestration.progress import stderr_progress_sink

    return stderr_progress_sink()


# ---------------------------------------------------------------------------
# Job dispatcher with concrete orchestrator dispatch
# ---------------------------------------------------------------------------


class JobDispatcher:
    """Serialize jobs per scenario_id using an asyncio.Semaphore(1) registry."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Semaphore] = {}

    def _lock(self, scenario_id: str) -> asyncio.Semaphore:
        if scenario_id not in self._locks:
            self._locks[scenario_id] = asyncio.Semaphore(1)
        return self._locks[scenario_id]

    def dispatch(
        self,
        background: BackgroundTasks,
        scenario_id: str,
        fn: JobFn,
    ) -> None:
        async def _run() -> None:
            async with self._lock(scenario_id):
                await fn()

        background.add_task(_run)

    # ── Concrete job dispatch methods ──────────────────────────────────

    def dispatch_verify(
        self,
        background: BackgroundTasks,
        *,
        scenario_id: str,
        revision_id: str,
        group_id: str | None = None,
    ) -> str:
        """Dispatch a verify job.

        Runs the stored scenario revision through the setup verifier in a
        background task.
        """
        from eval_harness.orchestration.setup import ScenarioSetupOrchestrator

        store = _get_store()
        revision = store.get_scenario_revision(revision_id)
        if revision is None:
            raise ValueError(f"Unknown scenario revision {revision_id}")
        if revision.scenario_id != scenario_id:
            raise ValueError(
                f"Scenario revision {revision_id} does not belong to scenario {scenario_id}"
            )

        scenario = store.get_scenario(scenario_id)
        if scenario is None:
            raise ValueError(f"Unknown scenario {scenario_id}")

        effective_group_id = group_id or f"api-{scenario.scenario_name}"

        async def _run() -> None:
            async with self._lock(scenario_id):
                backend, ctrl_factory = _build_backend_and_controller()
                planner = _build_planner()
                orch = ScenarioSetupOrchestrator(
                    backend=backend,
                    controller_factory=ctrl_factory,
                    planner=planner,
                    store=store,
                    progress=_job_progress_sink(),
                )
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: orch.run_existing_revision(
                        scenario_id=scenario_id,
                        revision_id=revision_id,
                        group_id=effective_group_id,
                    ),
                )

        background.add_task(_run)
        return scenario_id

    def dispatch_benchmark(
        self,
        background: BackgroundTasks,
        *,
        scenario_id: str,
        revision_id: str,
        setup_run_id: str,
        subject_ids: list[str] | None = None,
    ) -> str:
        """Dispatch a benchmark job."""
        from eval_harness.orchestration.benchmark import BenchmarkRunOrchestrator

        store = _get_store()
        setup_run = store.get_setup_run(setup_run_id)
        if setup_run is None:
            raise ValueError(f"Unknown setup run {setup_run_id}")
        if setup_run.status != "verified":
            raise ValueError(
                f"Setup run {setup_run_id} is not verified (status={setup_run.status})"
            )

        if subject_ids:
            active_subjects = store.list_active_subjects()
            active_ids = {s.id for s in active_subjects}
            for sid in subject_ids:
                if sid not in active_ids:
                    raise ValueError(f"Subject {sid} is not active or does not exist")

        async def _run() -> None:
            async with self._lock(scenario_id):
                backend, ctrl_factory = _build_backend_and_controller()
                subject_adapters = _build_subject_adapters()
                user_proxy_llm = _build_user_proxy_llm()
                config = _load_resolved_config()
                up_cfg = dict(config.get("user_proxy_llm", {}) or {})
                orch = BenchmarkRunOrchestrator(
                    backend=backend,
                    controller_factory=ctrl_factory,
                    subject_adapters=subject_adapters,
                    store=store,
                    user_proxy_llm=user_proxy_llm,
                    user_proxy_mode=str(up_cfg.get("mode", "pragmatic_human")),
                    progress=_job_progress_sink(),
                )
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: orch.run(
                        scenario_revision_id=revision_id,
                        verified_setup_run_id=setup_run_id,
                        subject_ids=subject_ids,
                    ),
                )

        background.add_task(_run)
        return scenario_id

    def dispatch_judge(
        self,
        background: BackgroundTasks,
        *,
        scenario_id: str,
        benchmark_run_id: str,
        mode: str = "absolute",
        anchor_subject: str | None = None,
    ) -> str:
        """Dispatch a judge job."""
        from eval_harness.orchestration.judge import JudgeJobOrchestrator

        store = _get_store()
        benchmark_run = store.get_benchmark_run(benchmark_run_id)
        if benchmark_run is None:
            raise ValueError(f"Unknown benchmark run {benchmark_run_id}")

        async def _run() -> None:
            async with self._lock(scenario_id):
                judges, weights = _build_judges_and_weights(mode=mode)
                orch = JudgeJobOrchestrator(judges=judges, store=store, weights=weights)
                loop = asyncio.get_running_loop()
                if mode == "pairwise":
                    await loop.run_in_executor(
                        None,
                        lambda: orch.run_pairwise(
                            benchmark_run_id=benchmark_run_id,
                            anchor_subject=anchor_subject,
                        ),
                    )
                else:
                    await loop.run_in_executor(
                        None,
                        lambda: orch.run_absolute(benchmark_run_id=benchmark_run_id),
                    )

        background.add_task(_run)
        return scenario_id

    def dispatch_run_all(
        self,
        background: BackgroundTasks,
        *,
        scenario_id: str,
        revision_id: str,
        group_id: str | None = None,
        subject_ids: list[str] | None = None,
        judge_mode: str = "absolute",
        judge_anchor_subject: str | None = None,
    ) -> str:
        """Chain verify -> benchmark -> judge in one background task."""
        from eval_harness.orchestration.benchmark import BenchmarkRunOrchestrator
        from eval_harness.orchestration.judge import JudgeJobOrchestrator
        from eval_harness.orchestration.setup import ScenarioSetupOrchestrator

        store = _get_store()
        revision = store.get_scenario_revision(revision_id)
        if revision is None:
            raise ValueError(f"Unknown scenario revision {revision_id}")
        if revision.scenario_id != scenario_id:
            raise ValueError(
                f"Scenario revision {revision_id} does not belong to scenario {scenario_id}"
            )

        scenario = store.get_scenario(scenario_id)
        if scenario is None:
            raise ValueError(f"Unknown scenario {scenario_id}")
        effective_group_id = group_id or f"api-{scenario.scenario_name}"
        up_cfg = dict(_load_resolved_config().get("user_proxy_llm", {}) or {})

        async def _run() -> None:
            async with self._lock(scenario_id):
                backend, ctrl_factory = _build_backend_and_controller()
                planner = _build_planner()
                subject_adapters = _build_subject_adapters()
                user_proxy_llm = _build_user_proxy_llm()
                progress = _job_progress_sink()
                loop = asyncio.get_running_loop()

                # 1. Verify
                orch_v = ScenarioSetupOrchestrator(
                    backend=backend,
                    controller_factory=ctrl_factory,
                    planner=planner,
                    store=store,
                    progress=progress,
                )
                result = await loop.run_in_executor(
                    None,
                    lambda: orch_v.run_existing_revision(
                        scenario_id=scenario_id,
                        revision_id=revision_id,
                        group_id=effective_group_id,
                    ),
                )
                setup_run_id = result.setup_run_id
                rev_id = result.scenario_revision_id or revision_id

                # 2. Benchmark
                orch_b = BenchmarkRunOrchestrator(
                    backend=backend,
                    controller_factory=ctrl_factory,
                    subject_adapters=subject_adapters,
                    store=store,
                    user_proxy_llm=user_proxy_llm,
                    user_proxy_mode=str(up_cfg.get("mode", "pragmatic_human")),
                    progress=progress,
                )
                bench_result = await loop.run_in_executor(
                    None,
                    lambda: orch_b.run(
                        scenario_revision_id=rev_id,
                        verified_setup_run_id=setup_run_id,
                        subject_ids=subject_ids,
                    ),
                )
                benchmark_run_id = bench_result.benchmark_run_id

                # 3. Judge
                judges, weights = _build_judges_and_weights(mode=judge_mode)
                orch_j = JudgeJobOrchestrator(
                    judges=judges, store=store, weights=weights
                )
                if judge_mode == "pairwise":
                    await loop.run_in_executor(
                        None,
                        lambda: orch_j.run_pairwise(
                            benchmark_run_id=benchmark_run_id,
                            anchor_subject=judge_anchor_subject,
                        ),
                    )
                else:
                    await loop.run_in_executor(
                        None,
                        lambda: orch_j.run_absolute(benchmark_run_id=benchmark_run_id),
                    )

        background.add_task(_run)
        return scenario_id


dispatcher = JobDispatcher()


def cancel_run(run_type: str, run_id: str) -> None:
    """Set a run's status to 'cancelled'.

    run_type must be 'setup', 'benchmark', or 'evaluation'.
    """
    from eval_harness.persistence.postgres_models import (
        BenchmarkRunRecord,
        EvaluationRunRecord,
        ScenarioSetupRunRecord,
    )
    from sqlalchemy import update

    store = _get_store()
    session_factory = store._session_factory
    with session_factory() as session:
        if run_type == "setup":
            session.execute(
                update(ScenarioSetupRunRecord)
                .where(ScenarioSetupRunRecord.id == run_id)
                .values(status="cancelled")
            )
        elif run_type == "benchmark":
            session.execute(
                update(BenchmarkRunRecord)
                .where(BenchmarkRunRecord.id == run_id)
                .values(status="cancelled")
            )
        elif run_type == "evaluation":
            session.execute(
                update(EvaluationRunRecord)
                .where(EvaluationRunRecord.id == run_id)
                .values(status="cancelled")
            )
        else:
            raise ValueError(f"Unknown run_type {run_type!r}")
        session.commit()
