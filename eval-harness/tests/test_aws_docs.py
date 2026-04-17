from __future__ import annotations

from pathlib import Path


def test_aws_docs_reference_canonical_packer_surface() -> None:
    packer_readme = (Path(__file__).resolve().parents[1] / "infra" / "aws" / "packer" / "README.md").read_text(encoding="utf-8")
    harness_readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    assert "debian-12-ssm-golden" in packer_readme
    assert "openclaw" not in packer_readme.lower()
    assert "debian-12-ssm-golden" in harness_readme
    assert "SSM" in harness_readme
    assert "must not refuse bounded sabotage" in harness_readme
    assert "controller.type" in harness_readme
    assert "ssm" in harness_readme
