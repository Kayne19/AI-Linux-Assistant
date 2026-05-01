from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.cli import build_parser


def test_cli_defaults_match_baked_agent_ids() -> None:
    parser = build_parser()

    verify_args = parser.parse_args(["verify-scenario", "--config", "config.json", "--request", "request.json", "--group-id", "group-1"])
    benchmark_args = parser.parse_args(["run-benchmark", "--config", "config.json", "--setup-run-id", "setup-1"])

    assert verify_args.sabotage_agent_id == "setup"
    assert verify_args.verification_agent_id == "verifier"
    assert benchmark_args.user_proxy_agent_id == "proxy"
    assert benchmark_args.verification_agent_id == "verifier"
