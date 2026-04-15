from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.backends.aws_packer import (
    AwsPackerBuildRequest,
    build_packer_commands,
    parse_manifest_ami_id,
    render_build_vars,
)


def _request() -> AwsPackerBuildRequest:
    return AwsPackerBuildRequest(
        target_image="debian-12-openclaw-golden",
        aws_region="us-west-2",
        subnet_id="subnet-123",
        iam_instance_profile="EvalSSMInstanceProfile",
        openclaw_eval_token="token-123",
        packer_template_dir=Path("/tmp/packer"),
        distro_vars_file=Path("/tmp/packer/distros/debian-12.pkrvars.hcl"),
    )


def test_render_build_vars_includes_expected_runtime_values(tmp_path: Path) -> None:
    manifest_path = tmp_path / "packer-manifest.json"
    rendered = render_build_vars(_request(), manifest_path)

    assert 'aws_region = "us-west-2"' in rendered
    assert 'subnet_id = "subnet-123"' in rendered
    assert 'iam_instance_profile = "EvalSSMInstanceProfile"' in rendered
    assert 'openclaw_eval_token = "token-123"' in rendered
    assert f'manifest_output = "{manifest_path}"' in rendered


def test_build_packer_commands_use_generated_and_distro_var_files(tmp_path: Path) -> None:
    request = _request()
    temp_vars_path = tmp_path / "generated.auto.pkrvars.hcl"

    init_command, build_command = build_packer_commands(request, temp_vars_path)

    assert init_command == ["packer", "init", "."]
    assert build_command == [
        "packer",
        "build",
        f"-var-file={temp_vars_path}",
        "-var-file=/tmp/packer/distros/debian-12.pkrvars.hcl",
        ".",
    ]


def test_parse_manifest_ami_id_reads_last_build_artifact(tmp_path: Path) -> None:
    manifest_path = tmp_path / "packer-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "builds": [
                    {"artifact_id": "us-west-2:ami-old"},
                    {"artifact_id": "us-west-2:ami-1234567890"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert parse_manifest_ami_id(manifest_path) == "ami-1234567890"
