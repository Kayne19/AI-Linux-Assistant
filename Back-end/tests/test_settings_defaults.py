from config.settings import load_settings


def test_magi_lite_defaults_to_low_reasoning(monkeypatch):
    for key in (
        "MAGI_LITE_EAGER_REASONING_EFFORT",
        "MAGI_LITE_SKEPTIC_REASONING_EFFORT",
        "MAGI_LITE_HISTORIAN_REASONING_EFFORT",
        "MAGI_LITE_ARBITER_REASONING_EFFORT",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings()

    assert settings.magi_lite_eager.reasoning_effort == "low"
    assert settings.magi_lite_skeptic.reasoning_effort == "low"
    assert settings.magi_lite_historian.reasoning_effort == "low"
    assert settings.magi_lite_arbiter.reasoning_effort == "low"


def test_magi_historian_and_wait_timeout_defaults(monkeypatch):
    for key in (
        "MAGI_HISTORIAN_PROVIDER",
        "MAGI_HISTORIAN_MODEL",
        "MAGI_HISTORIAN_REASONING_EFFORT",
        "CHAT_RUN_WAIT_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings()

    assert settings.magi_historian.provider == "openai"
    assert settings.magi_historian.model == "gpt-5.4"
    assert settings.magi_historian.reasoning_effort == "medium"
    assert settings.chat_run_wait_timeout_seconds == 1800


def test_retrieval_and_history_defaults(monkeypatch):
    for key in (
        "RETRIEVAL_INITIAL_FETCH",
        "RETRIEVAL_FINAL_TOP_K",
        "RETRIEVAL_NEIGHBOR_PAGES",
        "RETRIEVAL_MAX_EXPANDED",
        "RETRIEVAL_SOURCE_PROFILE_SAMPLE",
        "HISTORY_MAX_RECENT_TURNS",
        "HISTORY_SUMMARIZE_TURN_THRESHOLD",
        "HISTORY_SUMMARIZE_CHAR_THRESHOLD",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings()

    assert settings.retrieval_initial_fetch == 40
    assert settings.retrieval_final_top_k == 20
    assert settings.retrieval_neighbor_pages == 2
    assert settings.retrieval_max_expanded == 40
    assert settings.retrieval_source_profile_sample == 5000
    assert settings.history_max_recent_turns == 4
    assert settings.history_summarize_turn_threshold == 16
    assert settings.history_summarize_char_threshold == 3600
