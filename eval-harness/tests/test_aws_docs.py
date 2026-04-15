from __future__ import annotations

from pathlib import Path


def test_aws_docs_reference_canonical_packer_surface() -> None:
    packer_readme = (Path(__file__).resolve().parents[1] / "infra" / "aws" / "packer" / "README.md").read_text(encoding="utf-8")
    harness_readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    assert "18789" in packer_readme
    assert "packer-ami-openclaw" not in packer_readme
    assert "controller.remote_port" in harness_readme
    assert "18789" in harness_readme
    assert "controller.runtime" in harness_readme
    assert "gpt-5.4-mini" in packer_readme
    assert "must not refuse bounded sabotage" in harness_readme
    assert "cached AMIs still receive the current sandbox-sabotage" in packer_readme
