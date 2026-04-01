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
