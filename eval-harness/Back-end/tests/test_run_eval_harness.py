from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[3] / "run_eval_harness.py"
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


def test_runner_uses_conda_when_default_env_is_not_active(monkeypatch) -> None:
    module = _load_module()
    calls = []

    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name: "/usr/bin/conda" if name == "conda" else None,
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda command, cwd, env: (
            calls.append(command) or type("P", (), {"returncode": 0})()
        ),
    )
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)

    assert module._run_harness_command(["init-db"]) == 0
    assert calls[0][:5] == ["conda", "run", "-n", module.DEFAULT_CONDA_ENV, "python"]
