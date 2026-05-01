from __future__ import annotations

import time


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
