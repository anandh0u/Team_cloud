import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, load_settings
from app.main import create_app
from tests.conftest import FakeDetector


def _upload(http, data: bytes):
    return http.post("/vision/detect", files={"image": ("photo.jpg", data, "image/jpeg")})


def test_detect_returns_detections(vision_settings, mock_client):
    detector = FakeDetector()
    with TestClient(create_app(vision_settings, controller=mock_client, detector=detector)) as http:
        response = _upload(http, b"jpeg bytes")
    assert response.status_code == 200
    body = response.json()
    assert body["detections"][0] == {"label": "cell phone", "confidence": 0.91, "box": [20, 100, 120, 200]}
    assert body["image_width"] == 600
    assert detector.calls == [b"jpeg bytes"]


def test_detect_503_when_vision_disabled(settings, mock_client):
    assert settings.yolo_model is None
    with TestClient(create_app(settings, controller=mock_client)) as http:
        response = _upload(http, b"jpeg bytes")
    assert response.status_code == 503


def test_detect_rejects_unreadable_and_empty_images(vision_settings, mock_client):
    with TestClient(create_app(vision_settings, controller=mock_client, detector=FakeDetector())) as http:
        assert _upload(http, b"not an image").status_code == 400
        assert _upload(http, b"").status_code == 400


def test_phone_page_served(settings, mock_client):
    with TestClient(create_app(settings, controller=mock_client)) as http:
        response = http.get("/phone")
    assert response.status_code == 200
    assert "/assistant/phone" in response.text
    assert "__HTTPS_PORT__" not in response.text


@pytest.mark.parametrize("missing", ["YOLO_CONFIDENCE", "SCENE_MAX_AGE_S"])
def test_yolo_model_requires_confidence_and_scene_age(env, missing):
    env.update(YOLO_MODEL="data/models/yolo11n.pt", YOLO_CONFIDENCE="0.35", SCENE_MAX_AGE_S="60")
    del env[missing]
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


@pytest.mark.parametrize("value", ["0", "1.5", "high"])
def test_yolo_confidence_must_be_in_range(env, value):
    env.update(YOLO_MODEL="data/models/yolo11n.pt", YOLO_CONFIDENCE=value, SCENE_MAX_AGE_S="60")
    with pytest.raises(ConfigError, match="YOLO_CONFIDENCE"):
        load_settings(env)


def test_yolo_model_path_is_relative_to_project(env):
    env.update(YOLO_MODEL="data/models/yolo11n.pt", YOLO_CONFIDENCE="0.35", SCENE_MAX_AGE_S="60")
    settings = load_settings(env)
    assert settings.yolo_model.is_absolute()
    assert settings.yolo_model.parts[-3:] == ("data", "models", "yolo11n.pt")
    assert settings.yolo_confidence == 0.35
