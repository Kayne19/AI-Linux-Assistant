import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "Back-end"

# Load Back-end/.env before reading env vars so DATABASE_URL, REDIS_URL, and auth
# settings are available for both the API and the worker.
_dotenv_path = BACKEND_DIR / ".env"
if _dotenv_path.exists():
    try:
        from dotenv import load_dotenv as _load_dotenv

        _load_dotenv(_dotenv_path)
    except ImportError:
        pass

BACKEND_HOST = os.getenv("AILA_PUBLIC_API_HOST", "127.0.0.1")
BACKEND_PORT = os.getenv("AILA_PUBLIC_API_PORT", "8000")
CHAT_WORKER_ID = os.getenv("AILA_PUBLIC_WORKER_ID", "public-chat-worker")
CHAT_WORKER_PROCESS_COUNT = max(1, int(os.getenv("AILA_PUBLIC_WORKER_PROCESS_COUNT", "1")))
CHAT_WORKER_CONCURRENCY = max(1, int(os.getenv("AILA_PUBLIC_WORKER_CONCURRENCY", "4")))
START_CLOUDFLARED = os.getenv("AILA_PUBLIC_START_CLOUDFLARED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CLOUDFLARED_CONFIG = os.getenv("AILA_CLOUDFLARED_CONFIG", str(Path.home() / ".cloudflared" / "config.yml"))


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


def _spawn_process(label: str, command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
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
    redis_url = backend_env.get("REDIS_URL", "")
    if not redis_url:
        return None
    if shutil.which("redis-server") is None:
        print("[redis] REDIS_URL is set but redis-server not found on PATH — live fanout disabled")
        return None
    if _redis_already_running(redis_url):
        print(f"[redis] Redis already running at {redis_url}")
        return None
    parsed = urlparse(redis_url)
    port = str(parsed.port or 6379)
    print(f"[redis] Starting redis-server on port {port}")
    return _spawn_process("redis", ["redis-server", "--port", port], ROOT_DIR, backend_env)


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
    _require_path(BACKEND_DIR, "Back-end directory")

    backend_env = os.environ.copy()
    existing_pythonpath = backend_env.get("PYTHONPATH", "").strip()
    backend_env["PYTHONPATH"] = "app" if not existing_pythonpath else f"app{os.pathsep}{existing_pythonpath}"

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
    ]
    worker_command = [
        sys.executable,
        "app/chat_run_worker.py",
    ]

    print("Starting AI Linux Assistant public API stack")
    print(f"  backend      http://{BACKEND_HOST}:{BACKEND_PORT}")
    print(f"  workers      {CHAT_WORKER_ID} x {CHAT_WORKER_PROCESS_COUNT}")
    print(f"  worker slots {CHAT_WORKER_CONCURRENCY}")

    processes: list[tuple[str, subprocess.Popen[str]]] = []

    redis_proc = _maybe_start_redis(backend_env)
    if redis_proc is not None:
        processes.append(("redis", redis_proc))

    backend = _spawn_process("backend", backend_command, BACKEND_DIR, backend_env)
    processes.append(("backend", backend))

    for worker_index in range(CHAT_WORKER_PROCESS_COUNT):
        worker_env = backend_env.copy()
        worker_env["CHAT_RUN_WORKER_ID"] = f"{CHAT_WORKER_ID}-{worker_index + 1}"
        worker_env["CHAT_RUN_WORKER_CONCURRENCY"] = str(CHAT_WORKER_CONCURRENCY)
        worker = _spawn_process(f"worker-{worker_index + 1}", worker_command, BACKEND_DIR, worker_env)
        processes.append((f"worker-{worker_index + 1}", worker))

    if START_CLOUDFLARED:
        _require_command("cloudflared")
        cloudflared_config = Path(CLOUDFLARED_CONFIG).expanduser()
        _require_path(cloudflared_config, "cloudflared config")
        cloudflared = _spawn_process(
            "cloudflared",
            ["cloudflared", "tunnel", "--config", str(cloudflared_config), "run"],
            ROOT_DIR,
            os.environ.copy(),
        )
        processes.append(("cloudflared", cloudflared))
        print(f"  cloudflared  {cloudflared_config}")

    print("Press Ctrl+C to stop all processes.")

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
