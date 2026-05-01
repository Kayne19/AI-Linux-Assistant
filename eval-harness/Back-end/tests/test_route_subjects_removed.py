from fastapi.testclient import TestClient

from api.main import create_app


def test_subjects_endpoints_return_404():
    client = TestClient(create_app())
    assert client.get("/api/v1/subjects").status_code == 404
    assert client.get("/api/v1/subjects/adapter-types").status_code == 404
    assert client.get("/api/v1/subjects/x").status_code == 404
