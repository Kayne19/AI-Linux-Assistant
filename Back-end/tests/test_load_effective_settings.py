"""Unit tests for load_effective_settings and _apply_db_overrides.

These tests use a FakeRow object to simulate the AppSettingsModel ORM row
without touching a real database.
"""

from config.settings import (
    SETTINGS,
    RoleModelSettings,
    _apply_db_overrides,
    load_effective_settings,
)


class FakeRow:
    """Simulates an AppSettingsModel ORM row with all columns defaulting to None."""

    def __init__(self, **kwargs):
        # All 51 component columns default to None (DB NULL = use default)
        components = [
            "classifier",
            "contextualizer",
            "responder",
            "magi_eager",
            "magi_skeptic",
            "magi_historian",
            "magi_arbiter",
            "magi_lite_eager",
            "magi_lite_skeptic",
            "magi_lite_historian",
            "magi_lite_arbiter",
            "history_summarizer",
            "context_summarizer",
            "memory_extractor",
            "registry_updater",
            "ingest_enricher",
            "ingest_identity_normalizer",
            "chat_namer",
        ]
        for comp in components:
            setattr(self, f"{comp}_provider", None)
            setattr(self, f"{comp}_model", None)
            setattr(self, f"{comp}_reasoning_effort", None)
        for attr_name in (
            "retrieval_initial_fetch",
            "retrieval_final_top_k",
            "retrieval_neighbor_pages",
            "retrieval_max_expanded",
            "retrieval_source_profile_sample",
            "history_max_recent_turns",
            "history_summarize_turn_threshold",
            "history_summarize_char_threshold",
        ):
            setattr(self, attr_name, None)
        # Apply any overrides passed via kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_all_null_returns_base_settings():
    """When all columns are NULL, the result should be identical to SETTINGS."""
    row = FakeRow()
    result = _apply_db_overrides(SETTINGS, row)

    assert result.classifier == SETTINGS.classifier
    assert result.responder == SETTINGS.responder
    assert result.magi_eager == SETTINGS.magi_eager
    assert result.magi_lite_arbiter == SETTINGS.magi_lite_arbiter
    assert result.response_tool_rounds == SETTINGS.response_tool_rounds
    assert result.retrieval_initial_fetch == SETTINGS.retrieval_initial_fetch
    assert result.history_max_recent_turns == SETTINGS.history_max_recent_turns


def test_provider_and_model_override():
    """Non-null provider and model columns should override the defaults."""
    row = FakeRow(responder_provider="anthropic", responder_model="claude-opus-4-6")
    result = _apply_db_overrides(SETTINGS, row)

    assert result.responder.provider == "anthropic"
    assert result.responder.model == "claude-opus-4-6"
    assert result.responder.reasoning_effort == SETTINGS.responder.reasoning_effort
    # Other fields untouched
    assert result.classifier == SETTINGS.classifier


def test_empty_string_reasoning_effort_overrides():
    """An empty string reasoning_effort ('') should override the default (explicit no-effort)."""
    row = FakeRow(magi_eager_reasoning_effort="")
    result = _apply_db_overrides(SETTINGS, row)

    assert result.magi_eager.reasoning_effort == ""
    # Provider and model unchanged
    assert result.magi_eager.provider == SETTINGS.magi_eager.provider
    assert result.magi_eager.model == SETTINGS.magi_eager.model


def test_null_reasoning_effort_keeps_default():
    """NULL reasoning_effort should NOT override the default (keep base value)."""
    row = FakeRow()  # all None
    result = _apply_db_overrides(SETTINGS, row)

    # magi_eager default is "medium"
    assert result.magi_eager.reasoning_effort == SETTINGS.magi_eager.reasoning_effort


def test_magi_lite_override():
    """Overriding a magi_lite component should only affect that component."""
    row = FakeRow(
        magi_lite_skeptic_provider="local",
        magi_lite_skeptic_model="qwen2.5:7b",
        magi_lite_skeptic_reasoning_effort="high",
    )
    result = _apply_db_overrides(SETTINGS, row)

    assert result.magi_lite_skeptic == RoleModelSettings("local", "qwen2.5:7b", "high")
    assert result.magi_lite_eager == SETTINGS.magi_lite_eager
    assert result.magi_lite_historian == SETTINGS.magi_lite_historian


def test_retrieval_and_history_scalar_overrides():
    row = FakeRow(
        retrieval_initial_fetch=48,
        retrieval_final_top_k=16,
        retrieval_neighbor_pages=1,
        retrieval_max_expanded=32,
        retrieval_source_profile_sample=6400,
        history_max_recent_turns=6,
        history_summarize_turn_threshold=22,
        history_summarize_char_threshold=5000,
    )
    result = _apply_db_overrides(SETTINGS, row)

    assert result.retrieval_initial_fetch == 48
    assert result.retrieval_final_top_k == 16
    assert result.retrieval_neighbor_pages == 1
    assert result.retrieval_max_expanded == 32
    assert result.retrieval_source_profile_sample == 6400
    assert result.history_max_recent_turns == 6
    assert result.history_summarize_turn_threshold == 22
    assert result.history_summarize_char_threshold == 5000


def test_db_error_fallback(monkeypatch):
    """load_effective_settings should return SETTINGS if DB access raises."""
    import config.settings as settings_mod

    def broken_load():
        raise RuntimeError("DB connection refused")

    monkeypatch.setattr(settings_mod, "_load_settings_row", broken_load)
    result = load_effective_settings()

    assert result is SETTINGS


def test_none_row_returns_settings(monkeypatch):
    """When _load_settings_row returns None, load_effective_settings returns SETTINGS."""
    import config.settings as settings_mod

    monkeypatch.setattr(settings_mod, "_load_settings_row", lambda: None)
    result = load_effective_settings()

    assert result is SETTINGS


def test_full_override_all_components(monkeypatch):
    """A row with all components overridden should fully replace defaults."""
    components = [
        "classifier",
        "contextualizer",
        "responder",
        "magi_eager",
        "magi_skeptic",
        "magi_historian",
        "magi_arbiter",
        "magi_lite_eager",
        "magi_lite_skeptic",
        "magi_lite_historian",
        "magi_lite_arbiter",
        "history_summarizer",
        "context_summarizer",
        "memory_extractor",
        "registry_updater",
        "ingest_enricher",
        "chat_namer",
    ]
    overrides = {}
    for comp in components:
        overrides[f"{comp}_provider"] = "local"
        overrides[f"{comp}_model"] = "qwen2.5:7b"
        overrides[f"{comp}_reasoning_effort"] = "low"

    row = FakeRow(**overrides)

    import config.settings as settings_mod

    monkeypatch.setattr(settings_mod, "_load_settings_row", lambda: row)
    result = load_effective_settings()

    for comp in components:
        role: RoleModelSettings = getattr(result, comp)
        assert role.provider == "local", f"{comp}.provider should be 'local'"
        assert role.model == "qwen2.5:7b", f"{comp}.model should be 'qwen2.5:7b'"
        assert role.reasoning_effort == "low", (
            f"{comp}.reasoning_effort should be 'low'"
        )
