import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
HARNESS_DIR = ROOT_DIR / "eval-harness"
DEFAULT_CONFIG = HARNESS_DIR / "examples" / "aws_ai_linux_assistant_vs_chatgpt_config.json"
DEFAULT_REQUEST = HARNESS_DIR / "examples" / "planner_requests" / "cascading_complex_scenario.json"
DEFAULT_CONDA_ENV = "AI-Linux-Assistant"


def _require_path(path: Path, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"{label} not found: {path}")


def _build_command(args: list[str]) -> list[str]:
    active_conda_env = str(os.environ.get("CONDA_DEFAULT_ENV", "")).strip()
    if active_conda_env == DEFAULT_CONDA_ENV or shutil.which("conda") is None:
        return [sys.executable, "-m", "eval_harness", *args]
    return ["conda", "run", "-n", DEFAULT_CONDA_ENV, "python", "-m", "eval_harness", *args]


def _build_env() -> dict:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = "src" if not existing_pythonpath else f"src{os.pathsep}{existing_pythonpath}"
    return env


def _run_harness_command(args: list[str]) -> int:
    process = subprocess.run(_build_command(args), cwd=str(HARNESS_DIR), env=_build_env())
    return int(process.returncode)


def _run_harness_command_capture(args: list[str]) -> tuple[int, dict]:
    """Run a harness command, stream output to terminal, and return (exit_code, parsed_json)."""
    process = subprocess.run(
        _build_command(args),
        cwd=str(HARNESS_DIR),
        env=_build_env(),
        stdout=subprocess.PIPE,
        text=True,
    )
    if process.stdout:
        print(process.stdout, end="")
    result = {}
    if process.stdout and process.stdout.strip():
        try:
            result = json.loads(process.stdout.strip())
        except json.JSONDecodeError:
            pass
    return int(process.returncode), result


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


def _command_run_benchmark(args: argparse.Namespace) -> int:
    print(f"\n=== run-benchmark (setup_run_id={args.setup_run_id}) ===")
    exit_code, benchmark_result = _run_harness_command_capture([
        "run-benchmark",
        "--config", str(args.config),
        "--setup-run-id", args.setup_run_id,
    ])
    if exit_code != 0:
        return exit_code
    benchmark_run_id = benchmark_result.get("benchmark_run_id", "")
    if not benchmark_run_id:
        print("ERROR: run-benchmark did not return a benchmark_run_id.", file=sys.stderr)
        return 1
    print(f"\n=== run-judge-job (benchmark_run_id={benchmark_run_id}) ===")
    exit_code, _ = _run_harness_command_capture([
        "run-judge-job",
        "--config", str(args.config),
        "--benchmark-run-id", benchmark_run_id,
    ])
    return exit_code


def _command_smoke_test(args: argparse.Namespace) -> int:
    init_exit = _command_init_db(args)
    if init_exit != 0:
        return init_exit
    return _command_verify(args)


def _command_run(args: argparse.Namespace) -> int:
    print("=== Step 1/4: init-db ===")
    exit_code = _command_init_db(args)
    if exit_code != 0:
        return exit_code

    print("\n=== Step 2/4: verify-scenario ===")
    exit_code, verify_result = _run_harness_command_capture([
        "verify-scenario",
        "--config", str(args.config),
        "--request", str(args.request),
        "--group-id", args.group_id,
    ])
    if exit_code != 0:
        return exit_code
    setup_run_id = verify_result.get("setup_run_id", "")
    if not setup_run_id:
        print("ERROR: verify-scenario did not return a setup_run_id.", file=sys.stderr)
        return 1

    print(f"\n=== Step 3/4: run-benchmark (setup_run_id={setup_run_id}) ===")
    exit_code, benchmark_result = _run_harness_command_capture([
        "run-benchmark",
        "--config", str(args.config),
        "--setup-run-id", setup_run_id,
    ])
    if exit_code != 0:
        return exit_code
    benchmark_run_id = benchmark_result.get("benchmark_run_id", "")
    if not benchmark_run_id:
        print("ERROR: run-benchmark did not return a benchmark_run_id.", file=sys.stderr)
        return 1

    print(f"\n=== Step 4/4: run-judge-job (benchmark_run_id={benchmark_run_id}) ===")
    exit_code, _ = _run_harness_command_capture([
        "run-judge-job",
        "--config", str(args.config),
        "--benchmark-run-id", benchmark_run_id,
    ])
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_eval_harness.py",
        description="Run the eval harness. With no subcommand, runs the full pipeline end-to-end.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--request", type=Path, default=DEFAULT_REQUEST)
    parser.add_argument("--group-id", default=_default_group_id())
    parser.set_defaults(func=_command_run)
    subparsers = parser.add_subparsers(dest="command")

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

    benchmark_parser = subparsers.add_parser("run-benchmark", help="Run benchmark + judge from an existing setup run.")
    benchmark_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    benchmark_parser.add_argument("--setup-run-id", required=True)
    benchmark_parser.set_defaults(func=_command_run_benchmark)

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
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
