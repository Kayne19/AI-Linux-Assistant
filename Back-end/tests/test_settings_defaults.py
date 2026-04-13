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
