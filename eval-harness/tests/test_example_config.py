import json
from pathlib import Path


def test_aws_ai_linux_assistant_example_config_is_valid_json():
    config_path = Path(__file__).resolve().parents[1] / "examples" / "aws_ai_linux_assistant_config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    backend = payload["backend"]
    adapter = payload["subject_adapters"]["ai_linux_assistant_http"]
    controller = payload["controller"]
    planner = payload["planner"]
    judge = payload["judge"]
    user_proxy_llm = payload["user_proxy_llm"]

    assert adapter["base_url"] == "env:EVAL_HARNESS_AI_API_BASE_URL"
    assert "bearer_tokens_by_subject" in adapter
    assert "legacy_bootstrap_usernames_by_subject" not in adapter
    assert planner["provider"] == "openai"
    assert judge["provider"] == "openai"
    assert user_proxy_llm["provider"] == "openai"
    assert user_proxy_llm["model"] == "gpt-5.4"
    assert user_proxy_llm["mode"] == "pragmatic_human"
    assert "base_url" not in planner
    assert "base_url" not in judge
    assert "base_url" not in user_proxy_llm
    assert "reasoning_effort" not in user_proxy_llm
    assert "request_timeout_seconds" not in user_proxy_llm
    assert backend["default_target_image"] == "debian-12-ssm-golden"
    assert "golden_ami_id" not in backend
    assert backend["target_images"]["debian-12-ssm-golden"]["distro_vars_file"] == "infra/aws/packer/distros/debian-12.pkrvars.hcl"
    assert controller["type"] == "ssm"
    assert controller["aws_region"] == "env:EVAL_HARNESS_AWS_REGION"
    assert "max_turns" not in payload["subjects"][0]["adapter_config"]


def test_aws_ai_linux_assistant_vs_chatgpt_example_config_is_valid_json():
    config_path = Path(__file__).resolve().parents[1] / "examples" / "aws_ai_linux_assistant_vs_chatgpt_config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    adapters = payload["subject_adapters"]
    subjects = payload["subjects"]

    assert "ai_linux_assistant_http" in adapters
    assert "openai_chatgpt" in adapters
    assert adapters["openai_chatgpt"]["type"] == "openai_chatgpt"
    assert adapters["openai_chatgpt"]["api_key"] == "env:EVAL_HARNESS_CHATGPT_API_KEY"
    assert adapters["openai_chatgpt"]["model"] == "gpt-5.4"
    assert adapters["openai_chatgpt"]["conversation_state_mode"] == "conversation"
    assert adapters["openai_chatgpt"]["web_search_enabled"] is True
    assert adapters["openai_chatgpt"]["web_search_include_sources"] is True
    assert adapters["openai_chatgpt"]["code_interpreter_enabled"] is True
    assert adapters["openai_chatgpt"]["reasoning_effort"] == "medium"
    assert adapters["openai_chatgpt"]["truncation"] == "auto"
    assert "max_output_tokens" not in adapters["openai_chatgpt"]
    assert "request_timeout_seconds" not in adapters["openai_chatgpt"]
    assert "web_search_allowed_domains" not in adapters["openai_chatgpt"]
    assert payload["user_proxy_llm"]["model"] == "gpt-5.4"
    assert "reasoning_effort" not in payload["user_proxy_llm"]
    assert "request_timeout_seconds" not in payload["user_proxy_llm"]
    assert {subject["adapter_type"] for subject in subjects} == {"ai_linux_assistant_http", "openai_chatgpt"}
