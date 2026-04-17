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
    assert planner["type"] == "openai_responses"
    assert judge["type"] == "openai_responses"
    assert "base_url" not in planner
    assert "base_url" not in judge
    assert "base_url" not in user_proxy_llm
    assert backend["default_target_image"] == "debian-12-ssm-golden"
    assert "golden_ami_id" not in backend
    assert backend["target_images"]["debian-12-ssm-golden"]["distro_vars_file"] == "infra/aws/packer/distros/debian-12.pkrvars.hcl"
    assert controller["type"] == "ssm"
    assert controller["aws_region"] == "env:EVAL_HARNESS_AWS_REGION"
