import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency in some test/runtime environments
    def load_dotenv():
        return False


@dataclass(frozen=True)
class RoleModelSettings:
    provider: str
    model: str
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class AppSettings:
    provider_defaults: dict[str, str]
    classifier: RoleModelSettings
    contextualizer: RoleModelSettings
    responder: RoleModelSettings
    history_summarizer: RoleModelSettings
    context_summarizer: RoleModelSettings
    memory_extractor: RoleModelSettings
    registry_updater: RoleModelSettings
    ingest_enricher: RoleModelSettings = field(
        default_factory=lambda: RoleModelSettings("openai", "gpt-5.4-nano", "")
    )
    chat_namer: RoleModelSettings = field(
        default_factory=lambda: RoleModelSettings("openai", "gpt-5.4-nano", "")
    )
    response_tool_rounds: int = 8
    classifier_temperature: float = 0.0
    contextualizer_temperature: float = 0.0
    history_summarizer_temperature: float = 0.1
    history_max_recent_turns: int = 4
    history_summarize_turn_threshold: int = 16
    history_summarize_char_threshold: int = 3600
    magi_eager: RoleModelSettings = field(
        default_factory=lambda: RoleModelSettings("openai", "gpt-5.4", "medium")
    )
    magi_skeptic: RoleModelSettings = field(
        default_factory=lambda: RoleModelSettings("openai", "gpt-5.4", "high")
    )
    magi_historian: RoleModelSettings = field(
        default_factory=lambda: RoleModelSettings("anthropic", "claude-sonnet-4-6", "medium")
    )
    magi_arbiter: RoleModelSettings = field(
        default_factory=lambda: RoleModelSettings("openai", "gpt-5.4", "high")
    )
    magi_max_discussion_rounds: int = 3
    magi_lite_eager: RoleModelSettings = field(
        default_factory=lambda: RoleModelSettings("openai", "gpt-5.4-mini", "low")
    )
    magi_lite_skeptic: RoleModelSettings = field(
        default_factory=lambda: RoleModelSettings("openai", "gpt-5.4-mini", "medium")
    )
    magi_lite_historian: RoleModelSettings = field(
        default_factory=lambda: RoleModelSettings("openai", "gpt-5.4-mini", "medium")
    )
    magi_lite_arbiter: RoleModelSettings = field(
        default_factory=lambda: RoleModelSettings("openai", "gpt-5.4-mini", "low")
    )
    magi_lite_max_discussion_rounds: int = 2
    max_active_runs_per_user_default: int = 3
    chat_run_lease_seconds: int = 30
    chat_run_stream_poll_ms: int = 500
    chat_run_worker_poll_ms: int = 50
    chat_run_worker_concurrency: int = 3
    redis_url: str | None = None
    auth0_enabled: bool = True
    auth0_domain: str = ""
    auth0_issuer: str = ""
    auth0_audience: str = ""
    auth0_jwks_ttl_seconds: int = 300
    frontend_origins: tuple[str, ...] = ("http://localhost:5173",)
    enable_legacy_bootstrap_auth: bool = False


def _get_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _get_int_env(name, default):
    raw_value = _get_env(name, str(default))
    try:
        return int(raw_value)
    except ValueError:
        return default


def _get_float_env(name, default):
    raw_value = _get_env(name, str(default))
    try:
        return float(raw_value)
    except ValueError:
        return default


def _get_bool_env(name, default=False):
    raw_value = (_get_env(name, "true" if default else "false") or "").strip().lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    return default


def _get_csv_env(*names):
    for name in names:
        raw_value = os.getenv(name)
        if raw_value is None:
            continue
        items = tuple(part.strip() for part in raw_value.split(",") if part.strip())
        if items:
            return items
    return ()


def load_settings():
    load_dotenv()
    provider_defaults = {
        "openai": _get_env("OPENAI_DEFAULT_MODEL", "gpt-5.4-mini"),
        "anthropic": _get_env("ANTHROPIC_DEFAULT_MODEL", "claude-sonnet-4-6"),
        "local": _get_env("LOCAL_DEFAULT_MODEL", "qwen2.5:7b"),
    }
    return AppSettings(
        provider_defaults=provider_defaults,
        classifier=RoleModelSettings(
            provider=_get_env("CLASSIFIER_PROVIDER", "openai"),
            model=_get_env("CLASSIFIER_MODEL", "gpt-5.4-mini"),
            reasoning_effort=_get_env("CLASSIFIER_REASONING_EFFORT", ""),
        ),
        contextualizer=RoleModelSettings(
            provider=_get_env("CONTEXTUALIZER_PROVIDER", "openai"),
            model=_get_env("CONTEXTUALIZER_MODEL", "gpt-5.4-mini"),
            reasoning_effort=_get_env("CONTEXTUALIZER_REASONING_EFFORT", ""),
        ),
        responder=RoleModelSettings(
            provider=_get_env("RESPONDER_PROVIDER", "openai"),
            model=_get_env("RESPONDER_MODEL", "gpt-5.4"),
            reasoning_effort=_get_env("RESPONDER_REASONING_EFFORT", "low"),
        ),
        history_summarizer=RoleModelSettings(
            provider=_get_env("HISTORY_SUMMARIZER_PROVIDER", "openai"),
            model=_get_env("HISTORY_SUMMARIZER_MODEL", "gpt-5.4-mini"),
            reasoning_effort=_get_env("HISTORY_SUMMARIZER_REASONING_EFFORT", ""),
        ),
        context_summarizer=RoleModelSettings(
            provider=_get_env("CONTEXT_SUMMARIZER_PROVIDER", "openai"),
            model=_get_env("CONTEXT_SUMMARIZER_MODEL", "gpt-5.4-mini"),
            reasoning_effort=_get_env("CONTEXT_SUMMARIZER_REASONING_EFFORT", ""),
        ),
        memory_extractor=RoleModelSettings(
            provider=_get_env("MEMORY_EXTRACTOR_PROVIDER", "openai"),
            model=_get_env("MEMORY_EXTRACTOR_MODEL", "gpt-5.4-mini"),
            reasoning_effort=_get_env("MEMORY_EXTRACTOR_REASONING_EFFORT", ""),
        ),
        registry_updater=RoleModelSettings(
            provider=_get_env("REGISTRY_UPDATER_PROVIDER", "local"),
            model=_get_env("REGISTRY_UPDATER_MODEL", "qwen2.5:7b"),
            reasoning_effort=_get_env("REGISTRY_UPDATER_REASONING_EFFORT", ""),
        ),
        ingest_enricher=RoleModelSettings(
            provider=_get_env("INGEST_ENRICHER_PROVIDER", "openai"),
            model=_get_env("INGEST_ENRICHER_MODEL", "gpt-5.4-nano"),
            reasoning_effort=_get_env("INGEST_ENRICHER_REASONING_EFFORT", ""),
        ),
        chat_namer=RoleModelSettings(
            provider=_get_env("CHAT_NAMER_PROVIDER", "openai"),
            model=_get_env("CHAT_NAMER_MODEL", "gpt-5.4-nano"),
            reasoning_effort=_get_env("CHAT_NAMER_REASONING_EFFORT", ""),
        ),
        response_tool_rounds=_get_int_env("RESPONSE_TOOL_ROUNDS", 8),
        classifier_temperature=_get_float_env("CLASSIFIER_TEMPERATURE", 0.0),
        contextualizer_temperature=_get_float_env("CONTEXTUALIZER_TEMPERATURE", 0.0),
        history_summarizer_temperature=_get_float_env("HISTORY_SUMMARIZER_TEMPERATURE", 0.1),
        history_max_recent_turns=_get_int_env("HISTORY_MAX_RECENT_TURNS", 4),
        history_summarize_turn_threshold=_get_int_env("HISTORY_SUMMARIZE_TURN_THRESHOLD", 16),
        history_summarize_char_threshold=_get_int_env("HISTORY_SUMMARIZE_CHAR_THRESHOLD", 3600),
        magi_eager=RoleModelSettings(
            provider=_get_env("MAGI_EAGER_PROVIDER", "openai"),
            model=_get_env("MAGI_EAGER_MODEL", "gpt-5.4"),
            reasoning_effort=_get_env("MAGI_EAGER_REASONING_EFFORT", "medium"),
        ),
        magi_skeptic=RoleModelSettings(
            provider=_get_env("MAGI_SKEPTIC_PROVIDER", "openai"),
            model=_get_env("MAGI_SKEPTIC_MODEL", "gpt-5.4"),
            reasoning_effort=_get_env("MAGI_SKEPTIC_REASONING_EFFORT", "high"),
        ),
        magi_historian=RoleModelSettings(
            provider=_get_env("MAGI_HISTORIAN_PROVIDER", "openai"),
            model=_get_env("MAGI_HISTORIAN_MODEL", "gpt-5.4"),
            reasoning_effort=_get_env("MAGI_HISTORIAN_REASONING_EFFORT", "medium"),
        ),
        magi_arbiter=RoleModelSettings(
            provider=_get_env("MAGI_ARBITER_PROVIDER", "openai"),
            model=_get_env("MAGI_ARBITER_MODEL", "gpt-5.4"),
            reasoning_effort=_get_env("MAGI_ARBITER_REASONING_EFFORT", "high"),
        ),
        magi_max_discussion_rounds=_get_int_env("MAGI_MAX_DISCUSSION_ROUNDS", 3),
        magi_lite_eager=RoleModelSettings(
            provider=_get_env("MAGI_LITE_EAGER_PROVIDER", "openai"),
            model=_get_env("MAGI_LITE_EAGER_MODEL", "gpt-5.4-mini"),
            reasoning_effort=_get_env("MAGI_LITE_EAGER_REASONING_EFFORT", "low"),
        ),
        magi_lite_skeptic=RoleModelSettings(
            provider=_get_env("MAGI_LITE_SKEPTIC_PROVIDER", "openai"),
            model=_get_env("MAGI_LITE_SKEPTIC_MODEL", "gpt-5.4-mini"),
            reasoning_effort=_get_env("MAGI_LITE_SKEPTIC_REASONING_EFFORT", "medium"),
        ),
        magi_lite_historian=RoleModelSettings(
            provider=_get_env("MAGI_LITE_HISTORIAN_PROVIDER", "openai"),
            model=_get_env("MAGI_LITE_HISTORIAN_MODEL", "gpt-5.4-mini"),
            reasoning_effort=_get_env("MAGI_LITE_HISTORIAN_REASONING_EFFORT", "medium"),
        ),
        magi_lite_arbiter=RoleModelSettings(
            provider=_get_env("MAGI_LITE_ARBITER_PROVIDER", "openai"),
            model=_get_env("MAGI_LITE_ARBITER_MODEL", "gpt-5.4-mini"),
            reasoning_effort=_get_env("MAGI_LITE_ARBITER_REASONING_EFFORT", "low"),
        ),
        magi_lite_max_discussion_rounds=_get_int_env("MAGI_LITE_MAX_DISCUSSION_ROUNDS", 2),
        max_active_runs_per_user_default=_get_int_env("MAX_ACTIVE_RUNS_PER_USER_DEFAULT", 3),
        chat_run_lease_seconds=_get_int_env("CHAT_RUN_LEASE_SECONDS", 30),
        chat_run_stream_poll_ms=_get_int_env("CHAT_RUN_STREAM_POLL_MS", 500),
        chat_run_worker_poll_ms=_get_int_env("CHAT_RUN_WORKER_POLL_MS", 50),
        chat_run_worker_concurrency=_get_int_env("CHAT_RUN_WORKER_CONCURRENCY", 3),
        redis_url=_get_env("REDIS_URL", None) or None,
        auth0_enabled=_get_bool_env("AUTH0_ENABLED", True),
        auth0_domain=_get_env("AUTH0_DOMAIN", ""),
        auth0_issuer=_get_env("AUTH0_ISSUER", ""),
        auth0_audience=_get_env("AUTH0_AUDIENCE", ""),
        auth0_jwks_ttl_seconds=_get_int_env("AUTH0_JWKS_TTL_SECONDS", 300),
        frontend_origins=_get_csv_env("FRONTEND_ORIGINS", "FRONTEND_ORIGIN") or ("http://localhost:5173",),
        enable_legacy_bootstrap_auth=_get_bool_env("ENABLE_LEGACY_BOOTSTRAP_AUTH", False),
    )


# Composition root for the app. Change defaults here or override with .env.
SETTINGS = load_settings()
