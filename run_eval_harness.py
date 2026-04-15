import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
HARNESS_DIR = ROOT_DIR / "eval-harness"
DEFAULT_CONFIG = HARNESS_DIR / "examples" / "aws_ai_linux_assistant_config.json"
DEFAULT_REQUEST = HARNESS_DIR / "examples" / "planner_requests" / "nginx_recovery_request.json"
DEFAULT_CONDA_ENV = "AI-Linux-Assistant"


def _require_path(path: Path, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"{label} not found: {path}")


def _run_harness_command(args: list[str]) -> int:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = "src" if not existing_pythonpath else f"src{os.pathsep}{existing_pythonpath}"
    active_conda_env = str(env.get("CONDA_DEFAULT_ENV", "")).strip()
    if active_conda_env == DEFAULT_CONDA_ENV or shutil.which("conda") is None:
        command = [sys.executable, "-m", "eval_harness", *args]
    else:
        command = ["conda", "run", "-n", DEFAULT_CONDA_ENV, "python", "-m", "eval_harness", *args]
    process = subprocess.run(command, cwd=str(HARNESS_DIR), env=env)
    return int(process.returncode)


def _default_group_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"smoke-test-{stamp}"


def _command_init_db(args: argparse.Namespace) -> int:
    return _run_harness_command(["init-db", "--config", str(args.config)])


def _command_generate(args: argparse.Namespace) -> int:
    command = [
        "generate-scenario",
        "--config",
        str(args.config),
        "--request",
        str(args.request),
    ]
    if args.output:
        command.extend(["--output", str(args.output)])
    return _run_harness_command(command)


def _command_verify(args: argparse.Namespace) -> int:
    return _run_harness_command(
        [
            "verify-scenario",
            "--config",
            str(args.config),
            "--request",
            str(args.request),
            "--group-id",
            args.group_id,
        ]
    )


def _command_smoke_test(args: argparse.Namespace) -> int:
    init_exit = _command_init_db(args)
    if init_exit != 0:
        return init_exit
    return _command_verify(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_eval_harness.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-db", help="Initialize the eval-harness database schema.")
    init_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    init_parser.set_defaults(func=_command_init_db)

    generate_parser = subparsers.add_parser("generate-scenario", help="Generate a scenario from the default harness request.")
    generate_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    generate_parser.add_argument("--request", type=Path, default=DEFAULT_REQUEST)
    generate_parser.add_argument("--output", type=Path, default=None)
    generate_parser.set_defaults(func=_command_generate)

    verify_parser = subparsers.add_parser("verify-scenario", help="Run scenario setup and verification.")
    verify_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    verify_parser.add_argument("--request", type=Path, default=DEFAULT_REQUEST)
    verify_parser.add_argument("--group-id", default=_default_group_id())
    verify_parser.set_defaults(func=_command_verify)

    smoke_parser = subparsers.add_parser(
        "smoke-test",
        help="Initialize the DB and run the default verify-scenario smoke test.",
    )
    smoke_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    smoke_parser.add_argument("--request", type=Path, default=DEFAULT_REQUEST)
    smoke_parser.add_argument("--group-id", default=_default_group_id())
    smoke_parser.set_defaults(func=_command_smoke_test)

    return parser


def main() -> int:
    _require_path(HARNESS_DIR, "eval-harness directory")
    _require_path(DEFAULT_CONFIG, "default eval-harness config")
    _require_path(DEFAULT_REQUEST, "default planner request")
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
