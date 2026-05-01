import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "Back-end"
FRONTEND_DIR = ROOT_DIR / "Front-end"

# Load Back-end/.env before reading env vars so REDIS_URL and others are available.
# Use the project's canonical .env resolution so the same file is picked up as
# the backend entry points.
_sys_path_restore = sys.path.copy()
try:
    sys.path.insert(0, str(BACKEND_DIR / "app"))
    from utils.env import load_project_dotenv  # noqa: E402

    load_project_dotenv(start_dir=BACKEND_DIR)
finally:
    sys.path[:] = _sys_path_restore

BACKEND_HOST = os.getenv("AILA_BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = os.getenv("AILA_BACKEND_PORT", "8000")
FRONTEND_HOST = os.getenv("AILA_FRONTEND_HOST", "0.0.0.0")
FRONTEND_PORT = os.getenv("AILA_FRONTEND_PORT", "5173")
CHAT_WORKER_ID = os.getenv("CHAT_RUN_WORKER_ID", "dev-chat-worker")
CHAT_WORKER_PROCESS_COUNT = max(1, int(os.getenv("CHAT_RUN_WORKER_PROCESS_COUNT", "4")))

EVAL_HARNESS_BACKEND_HOST = os.getenv("EVAL_HARNESS_BACKEND_HOST", "0.0.0.0")
EVAL_HARNESS_BACKEND_PORT = os.getenv("EVAL_HARNESS_BACKEND_PORT", "8001")
EVAL_HARNESS_FRONTEND_HOST = os.getenv("EVAL_HARNESS_FRONTEND_HOST", "0.0.0.0")
EVAL_HARNESS_FRONTEND_PORT = os.getenv("EVAL_HARNESS_FRONTEND_PORT", "5174")

EVAL_HARNESS_DIR = ROOT_DIR / "eval-harness"
EVAL_HARNESS_BACKEND_DIR = EVAL_HARNESS_DIR / "Back-end"
EVAL_HARNESS_FRONTEND_DIR = EVAL_HARNESS_DIR / "Front-end"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the AI Linux Assistant local development stack.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--frontend-only",
        action="store_true",
        help="Start only the Vite frontend. Use this when the API is already running.",
    )
    mode_group.add_argument(
        "--backend-only",
        action="store_true",
        help="Start the API plus chat workers, but skip the frontend.",
    )
    mode_group.add_argument(
        "--api-only",
        action="store_true",
        help="Start only the API and skip workers/frontend.",
    )
    mode_group.add_argument(
        "--workers-only",
        action="store_true",
        help="Start only chat workers and skip API/frontend.",
    )
    mode_group.add_argument(
        "--eval-harness",
        action="store_true",
        help="Start the chatbot stack AND the eval harness stack.",
    )
    mode_group.add_argument(
        "--eval-only",
        action="store_true",
        help="Start only the eval harness stack (no chatbot).",
    )
    parser.add_argument(
        "--skip-backend",
        action="store_true",
        help="Skip the API process.",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Skip the Vite frontend process.",
    )
    parser.add_argument(
        "--skip-workers",
        action="store_true",
        help="Skip chat worker processes.",
    )
    return parser.parse_args()


def _require_path(path: Path, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"{label} not found: {path}")


def _require_command(command: str) -> None:
    if shutil.which(command) is None:
        raise SystemExit(f"Required command not found on PATH: {command}")


def _stream_output(label: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{label}] {line.rstrip()}")


def _is_port_in_use(host: str, port: str) -> bool:
    bind_host = host
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, int(port)))
        except OSError:
            return True
    return False


def _ensure_port_available(host: str, port: str, label: str, hint: str) -> None:
    if _is_port_in_use(host, port):
        raise SystemExit(f"{label} port {port} is already in use on {host}. {hint}")


def _spawn_process(
    label: str, command: list[str], cwd: Path, env: dict[str, str]
) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    threading.Thread(target=_stream_output, args=(label, process), daemon=True).start()
    return process


def _redis_already_running(redis_url: str) -> bool:
    """Return True if a Redis server is already reachable at redis_url."""
    if shutil.which("redis-cli") is None:
        return False
    try:
        result = subprocess.run(
            ["redis-cli", "-u", redis_url, "ping"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.returncode == 0 and "PONG" in result.stdout
    except Exception:
        return False


def _maybe_start_redis(backend_env: dict[str, str]) -> "subprocess.Popen[str] | None":
    """Start redis-server if REDIS_URL is configured and Redis is not already reachable."""
    redis_url = backend_env.get("REDIS_URL", "")
    if not redis_url:
        return None
    if shutil.which("redis-server") is None:
        print(
            "[redis] REDIS_URL is set but redis-server not found on PATH — live fanout disabled"
        )
        return None
    if _redis_already_running(redis_url):
        print(f"[redis] Redis already running at {redis_url}")
        return None
    parsed = urlparse(redis_url)
    port = str(parsed.port or 6379)
    print(f"[redis] Starting redis-server on port {port}")
    return _spawn_process(
        "redis", ["redis-server", "--port", port], ROOT_DIR, backend_env
    )


def _terminate_process(process: subprocess.Popen[str], label: str) -> None:
    if process.poll() is not None:
        return
    print(f"Stopping {label}...")
    process.terminate()
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> None:
    args = _parse_args()
    _require_path(BACKEND_DIR, "Back-end directory")
    _require_path(FRONTEND_DIR, "Front-end directory")

    backend_env = os.environ.copy()
    existing_pythonpath = backend_env.get("PYTHONPATH", "").strip()
    backend_env["PYTHONPATH"] = (
        "app" if not existing_pythonpath else f"app{os.pathsep}{existing_pythonpath}"
    )

    frontend_env = os.environ.copy()
    frontend_env.setdefault("BROWSER", "none")

    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "api:create_app",
        "--factory",
        "--app-dir",
        "app",
        "--host",
        BACKEND_HOST,
        "--port",
        BACKEND_PORT,
        "--reload",
    ]
    frontend_command = [
        "npm",
        "run",
        "dev",
        "--",
        "--host",
        FRONTEND_HOST,
        "--port",
        FRONTEND_PORT,
    ]
    worker_command = [
        sys.executable,
        "app/chat_run_worker.py",
    ]

    start_backend = not args.skip_backend
    start_frontend = not args.skip_frontend
    start_workers = not args.skip_workers

    start_eval_backend = False
    start_eval_frontend = False

    if args.frontend_only:
        start_backend = False
        start_frontend = True
        start_workers = False
    elif args.backend_only:
        start_backend = True
        start_frontend = False
        start_workers = True
    elif args.api_only:
        start_backend = True
        start_frontend = False
        start_workers = False
    elif args.workers_only:
        start_backend = False
        start_frontend = False
        start_workers = True
    elif args.eval_harness:
        start_backend = True
        start_frontend = True
        start_workers = True
        start_eval_backend = True
        start_eval_frontend = True
    elif args.eval_only:
        start_backend = False
        start_frontend = False
        start_workers = False
        start_eval_backend = True
        start_eval_frontend = True

    if not any(
        (
            start_backend,
            start_frontend,
            start_workers,
            start_eval_backend,
            start_eval_frontend,
        )
    ):
        raise SystemExit(
            "Nothing selected to start. Remove the skip flags or choose a mode like --frontend-only."
        )

    if start_frontend:
        _require_command("npm")
        _ensure_port_available(
            FRONTEND_HOST,
            FRONTEND_PORT,
            "Frontend",
            "Use --skip-frontend, change AILA_FRONTEND_PORT, or stop the other process.",
        )
    if start_backend:
        _ensure_port_available(
            BACKEND_HOST,
            BACKEND_PORT,
            "Backend",
            "Use --frontend-only/--skip-backend, change AILA_BACKEND_PORT, or stop the other API process.",
        )

    print("Starting AI Linux Assistant dev stack")
    print(f"  backend  {'enabled' if start_backend else 'disabled'}", end="")
    if start_backend:
        print(f"  http://{BACKEND_HOST}:{BACKEND_PORT}")
    else:
        print()
    print(f"  frontend {'enabled' if start_frontend else 'disabled'}", end="")
    if start_frontend:
        print(f"  http://{FRONTEND_HOST}:{FRONTEND_PORT}")
    else:
        print()
    print(
        f"  workers  {CHAT_WORKER_ID} x {CHAT_WORKER_PROCESS_COUNT}"
        if start_workers
        else "  workers  disabled"
    )

    processes: list[tuple[str, subprocess.Popen[str]]] = []

    redis_proc = (
        _maybe_start_redis(backend_env) if (start_backend or start_workers) else None
    )
    if redis_proc is not None:
        processes.append(("redis", redis_proc))

    if start_backend:
        backend = _spawn_process("backend", backend_command, BACKEND_DIR, backend_env)
        processes.append(("backend", backend))
    if start_frontend:
        frontend = _spawn_process(
            "frontend", frontend_command, FRONTEND_DIR, frontend_env
        )
        processes.append(("frontend", frontend))
    if start_workers:
        for worker_index in range(CHAT_WORKER_PROCESS_COUNT):
            worker_env = backend_env.copy()
            worker_env["CHAT_RUN_WORKER_ID"] = f"{CHAT_WORKER_ID}-{worker_index + 1}"
            worker_env.setdefault("CHAT_RUN_WORKER_CONCURRENCY", "1")
            worker = _spawn_process(
                f"worker-{worker_index + 1}", worker_command, BACKEND_DIR, worker_env
            )
            processes.append((f"worker-{worker_index + 1}", worker))

    if start_eval_backend or start_eval_frontend:
        _require_path(EVAL_HARNESS_BACKEND_DIR, "Eval harness Back-end directory")
        _require_path(EVAL_HARNESS_FRONTEND_DIR, "Eval harness Front-end directory")

    if start_eval_backend:
        _ensure_port_available(
            EVAL_HARNESS_BACKEND_HOST,
            EVAL_HARNESS_BACKEND_PORT,
            "Eval harness backend",
            "Change EVAL_HARNESS_BACKEND_PORT or stop the other process.",
        )
        eval_backend_env = os.environ.copy()
        eval_backend_env["PYTHONPATH"] = "eval_harness"
        eval_backend_command = [
            sys.executable,
            "-m",
            "uvicorn",
            "api.main:create_app",
            "--factory",
            "--app-dir",
            "api",
            "--host",
            EVAL_HARNESS_BACKEND_HOST,
            "--port",
            EVAL_HARNESS_BACKEND_PORT,
            "--reload",
        ]
        eval_backend = _spawn_process(
            "eval-backend",
            eval_backend_command,
            EVAL_HARNESS_BACKEND_DIR,
            eval_backend_env,
        )
        processes.append(("eval-backend", eval_backend))

    if start_eval_frontend:
        _require_command("npm")
        _ensure_port_available(
            EVAL_HARNESS_FRONTEND_HOST,
            EVAL_HARNESS_FRONTEND_PORT,
            "Eval harness frontend",
            "Change EVAL_HARNESS_FRONTEND_PORT or stop the other process.",
        )
        eval_frontend_env = os.environ.copy()
        eval_frontend_env.setdefault("BROWSER", "none")
        eval_frontend_command = [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            EVAL_HARNESS_FRONTEND_HOST,
            "--port",
            EVAL_HARNESS_FRONTEND_PORT,
        ]
        eval_frontend = _spawn_process(
            "eval-frontend",
            eval_frontend_command,
            EVAL_HARNESS_FRONTEND_DIR,
            eval_frontend_env,
        )
        processes.append(("eval-frontend", eval_frontend))

    def _shutdown(*_args) -> None:
        for label, process in reversed(processes):
            _terminate_process(process, label)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            for label, process in processes:
                exit_code = process.poll()
                if exit_code is None:
                    continue
                print(f"{label} exited with code {exit_code}")
                _shutdown()
            time.sleep(0.5)
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
