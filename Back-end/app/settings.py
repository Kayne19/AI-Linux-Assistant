import os
from dataclasses import dataclass

from dotenv import load_dotenv


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
    response_tool_rounds: int = 8
    classifier_temperature: float = 0.0
    contextualizer_temperature: float = 0.0
    history_summarizer_temperature: float = 0.1
    history_max_recent_turns: int = 4
    history_summarize_turn_threshold: int = 16
    history_summarize_char_threshold: int = 3600


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


def load_settings():
    load_dotenv()
    provider_defaults = {
        "openai": _get_env("OPENAI_DEFAULT_MODEL", "gpt-5.4-mini"),
        "local": _get_env("LOCAL_DEFAULT_MODEL", "qwen2.5:7b"),
        "gemini": _get_env("GEMINI_DEFAULT_MODEL", "gemma-3-27b"),
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
        response_tool_rounds=_get_int_env("RESPONSE_TOOL_ROUNDS", 8),
        classifier_temperature=_get_float_env("CLASSIFIER_TEMPERATURE", 0.0),
        contextualizer_temperature=_get_float_env("CONTEXTUALIZER_TEMPERATURE", 0.0),
        history_summarizer_temperature=_get_float_env("HISTORY_SUMMARIZER_TEMPERATURE", 0.1),
        history_max_recent_turns=_get_int_env("HISTORY_MAX_RECENT_TURNS", 4),
        history_summarize_turn_threshold=_get_int_env("HISTORY_SUMMARIZE_TURN_THRESHOLD", 16),
        history_summarize_char_threshold=_get_int_env("HISTORY_SUMMARIZE_CHAR_THRESHOLD", 3600),
    )


# Composition root for the app. Change defaults here or override with .env.
SETTINGS = load_settings()
