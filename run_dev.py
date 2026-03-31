import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "Back-end"
FRONTEND_DIR = ROOT_DIR / "Front-end"

BACKEND_HOST = os.getenv("AILA_BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = os.getenv("AILA_BACKEND_PORT", "8000")
FRONTEND_HOST = os.getenv("AILA_FRONTEND_HOST", "0.0.0.0")
FRONTEND_PORT = os.getenv("AILA_FRONTEND_PORT", "5173")
CHAT_WORKER_ID = os.getenv("CHAT_RUN_WORKER_ID", "dev-chat-worker")
CHAT_WORKER_PROCESS_COUNT = max(1, int(os.getenv("CHAT_RUN_WORKER_PROCESS_COUNT", "4")))


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
    _require_path(FRONTEND_DIR, "Front-end directory")
    _require_command("npm")

    backend_env = os.environ.copy()
    existing_pythonpath = backend_env.get("PYTHONPATH", "").strip()
    backend_env["PYTHONPATH"] = "app" if not existing_pythonpath else f"app{os.pathsep}{existing_pythonpath}"

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

    print("Starting AI Linux Assistant dev stack")
    print(f"  backend  http://{BACKEND_HOST}:{BACKEND_PORT}")
    print(f"  frontend http://{FRONTEND_HOST}:{FRONTEND_PORT}")
    print(f"  workers  {CHAT_WORKER_ID} x {CHAT_WORKER_PROCESS_COUNT}")

    backend = _spawn_process("backend", backend_command, BACKEND_DIR, backend_env)
    frontend = _spawn_process("frontend", frontend_command, FRONTEND_DIR, frontend_env)
    processes = [
        ("backend", backend),
        ("frontend", frontend),
    ]
    for worker_index in range(CHAT_WORKER_PROCESS_COUNT):
        worker_env = backend_env.copy()
        worker_env["CHAT_RUN_WORKER_ID"] = f"{CHAT_WORKER_ID}-{worker_index + 1}"
        worker_env.setdefault("CHAT_RUN_WORKER_CONCURRENCY", "1")
        worker = _spawn_process(f"worker-{worker_index + 1}", worker_command, BACKEND_DIR, worker_env)
        processes.append((f"worker-{worker_index + 1}", worker))

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
