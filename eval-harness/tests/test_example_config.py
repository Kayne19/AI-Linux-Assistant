import json
from pathlib import Path


def test_aws_ai_linux_assistant_example_config_is_valid_json():
    config_path = Path(__file__).resolve().parents[1] / "examples" / "aws_ai_linux_assistant_config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    adapter = payload["subject_adapters"]["ai_linux_assistant_http"]

    assert adapter["base_url"] == "env:EVAL_HARNESS_AI_API_BASE_URL"
    assert "bearer_tokens_by_subject" in adapter
    assert "legacy_bootstrap_usernames_by_subject" not in adapter
