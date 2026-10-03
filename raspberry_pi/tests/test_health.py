from fastapi.testclient import TestClient

from app.main import create_app


def test_health_ok_in_mock_mode(settings, mock_client):
    with TestClient(create_app(settings, controller=mock_client)) as http:
        body = http.get("/health").json()
    assert body["status"] == "ok"
    assert body["mock_hardware"] is True
    assert body["components"][0] == {"name": "esp32_controller", "reachable": True, "source": "MOCK",
                                      "latency_ms": 0.0, "error": None}


def test_health_degraded_when_controller_down(settings, mock_client):
    mock_client.available = False
    with TestClient(create_app(settings, controller=mock_client)) as http:
        response = http.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"][0]["reachable"] is False
