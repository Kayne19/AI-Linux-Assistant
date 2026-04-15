from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "run_eval_harness.py"
    spec = importlib.util.spec_from_file_location("run_eval_harness", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_defaults_to_smoke_test_paths() -> None:
    module = _load_module()
    parser = module.build_parser()
    args = parser.parse_args(["smoke-test"])

    assert args.config == module.DEFAULT_CONFIG
    assert args.request == module.DEFAULT_REQUEST
    assert args.group_id.startswith("smoke-test-")


def test_runner_exposes_verify_command_defaults() -> None:
    module = _load_module()
    parser = module.build_parser()
    args = parser.parse_args(["verify-scenario"])

    assert args.config == module.DEFAULT_CONFIG
    assert args.request == module.DEFAULT_REQUEST
    assert args.group_id.startswith("smoke-test-")
