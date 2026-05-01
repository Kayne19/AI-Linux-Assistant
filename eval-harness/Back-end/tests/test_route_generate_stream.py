import json

from fastapi.testclient import TestClient

from api.main import create_app


def _stream_payload():
    return {
        "planning_brief": "deploy nginx with health check",
        "target_image": "ubuntu-22.04",
        "scenario_name_hint": "deploy_nginx",
        "tags": ["network"],
        "constraints": [],
    }


def test_generate_stream_returns_event_stream(monkeypatch):
    """The streaming endpoint emits SSE chunks and a final scenario event."""
    from api.routes import generate as gen_module

    class FakePlanner:
        def stream_scenario(self, request):
            yield {"type": "token", "text": "{"}
            yield {"type": "token", "text": '"name":"deploy_nginx"'}
            yield {"type": "token", "text": "}"}
            yield {"type": "scenario", "scenario": {"name": "deploy_nginx"}}

    monkeypatch.setattr(gen_module, "_resolve_planner", lambda: FakePlanner())

    client = TestClient(create_app())
    with client.stream(
        "POST", "/api/v1/scenarios/generate/stream", json=_stream_payload()
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        chunks = [line for line in response.iter_lines() if line.startswith("data:")]

    assert len(chunks) >= 4
    final = json.loads(chunks[-1].removeprefix("data:").strip())
    assert final["type"] == "scenario"
    assert final["scenario"]["name"] == "deploy_nginx"
