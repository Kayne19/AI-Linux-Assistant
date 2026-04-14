from __future__ import annotations

import shutil
import socket
import subprocess
import time
from dataclasses import dataclass


def wait_for_ssm_online(ssm_client, instance_id: str, timeout_seconds: int = 600, poll_seconds: int = 10) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = ssm_client.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}],
        )
        info = response.get("InstanceInformationList", [])
        if info and info[0].get("PingStatus") == "Online":
            return
        time.sleep(poll_seconds)
    raise TimeoutError(f"Timed out waiting for SSM availability on instance {instance_id}.")


def _wait_for_local_port(local_port: int, timeout_seconds: int = 30) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("127.0.0.1", local_port)) == 0:
                return
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for local port {local_port} to accept connections.")


@dataclass
class SsmPortForwardSession:
    instance_id: str
    local_port: int
    remote_port: int
    region: str
    process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if shutil.which("aws") is None:  # pragma: no cover - environment dependent
            raise RuntimeError("AWS CLI is required for SSM port forwarding.")
        command = [
            "aws",
            "ssm",
            "start-session",
            "--region",
            self.region,
            "--target",
            self.instance_id,
            "--document-name",
            "AWS-StartPortForwardingSession",
            "--parameters",
            (
                '{"portNumber":["%s"],"localPortNumber":["%s"]}'
                % (self.remote_port, self.local_port)
            ),
        ]
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_for_local_port(self.local_port)

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - environment dependent
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            self.process = None

    def __enter__(self) -> "SsmPortForwardSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
