import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, load_settings
from app.main import create_app
from app.models import PersonPose, PoseObservation
from app.patient.condition import ConditionTracker, movement_between, posture_of
from app.patient.monitor import PatientMonitor
from tests.conftest import FakeDetector


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def person(shoulder=(100, 100), hip=(100, 200), shift=0.0, confidence=0.9) -> PersonPose:
    """17 keypoints; shoulders (5, 6) and hips (11, 12) placed as given, the rest spread
    along the body. `shift` moves every keypoint right by that many pixels."""
    kps = [(50.0 + i * 10 + shift, 150.0, 0.9) for i in range(17)]
    for i in (5, 6):
        kps[i] = (shoulder[0] + shift, shoulder[1], 0.9)
    for i in (11, 12):
        kps[i] = (hip[0] + shift, hip[1], 0.9)
    return PersonPose(confidence=confidence, box=(0, 0, 300, 400), keypoints=kps)


def seen(*people: PersonPose) -> PoseObservation:
    return PoseObservation(image_width=640, image_height=480, people=list(people))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def tracker(responses, clock):
    return ConditionTracker(responses, still_attention_s=600, away_attention_s=300, camera_timeout_s=15,
                            sensor_timeout_s=30, clock=clock)


# ---------------------------------------------------------------- posture + movement

@pytest.mark.parametrize("shoulder, hip, posture", [
    ((100, 100), (100, 200), "upright"),
    ((100, 100), (200, 120), "lying"),
    ((100, 100), (170, 170), "reclined"),
])
def test_posture_from_torso_angle(shoulder, hip, posture):
    assert posture_of(person(shoulder, hip)) == posture


def test_posture_unknown_without_hips():
    p = person()
    p.keypoints[11] = p.keypoints[12] = (0, 0, 0.1)
    assert posture_of(p) == "unknown"


def test_movement_is_relative_to_body_size():
    assert movement_between(person(), person(shift=2)) < 0.03    # jitter
    assert movement_between(person(), person(shift=40)) > 0.03   # real movement


# ---------------------------------------------------------------- tracker

def test_still_patient_needs_attention_after_threshold(tracker, clock, responses):
    tracker.observe(seen(person()))
    for _ in range(11):  # one nearly identical frame every minute
        clock.now += 60
        tracker.observe(seen(person(shift=1)))
    c = tracker.condition()
    assert c.camera.person_in_view and c.camera.still_for_s == 660
    assert c.attention_keys == ["attention_still", "attention_sensors_off"]
    assert c.attention[0] == responses.get("attention_still", minutes="11")


def test_movement_resets_still_timer(tracker, clock):
    tracker.observe(seen(person()))
    clock.now += 700
    tracker.observe(seen(person(shift=50)))
    assert tracker.condition().camera.still_for_s == 0
    assert "attention_still" not in tracker.condition().attention_keys


def test_not_in_view(tracker, clock):
    tracker.observe(seen(person()))
    tracker.observe(seen())
    clock.now += 301
    tracker.observe(seen(person(confidence=0.2)))  # too unsure to count
    c = tracker.condition()
    assert c.camera.person_in_view is False and c.camera.not_in_view_for_s == 301
    assert "attention_away" in c.attention_keys


def test_camera_goes_offline(tracker, clock):
    tracker.observe(seen(person()))
    clock.now += 16
    c = tracker.condition()
    assert not c.camera.available and "attention_camera_off" in c.attention_keys


def test_camera_monitoring_off_means_no_camera_notes(responses, clock):
    tracker = ConditionTracker(responses, None, None, camera_timeout_s=None, sensor_timeout_s=None, clock=clock)
    c = tracker.condition()
    assert not c.camera.available and c.attention == []
    assert "polling is off" in c.sensors.errors[0]


def test_sensor_readings_and_staleness(tracker, clock, mock_client):
    from app.hardware.telemetry import TelemetryService
    tracker.update_sensors(asyncio.run(TelemetryService(mock_client).snapshot()))
    s = tracker.condition().sensors
    assert s.available and s.source.value == "MOCK" and s.heartbeat_bpm is not None
    clock.now += 31
    s = tracker.condition().sensors
    assert not s.available and s.errors == ["reading is out of date"]


# ---------------------------------------------------------------- monitor

class FakePose:
    def __init__(self):
        self.calls = 0

    async def observe(self, image: bytes) -> PoseObservation:
        self.calls += 1
        return seen(person())


def test_pose_runs_at_most_once_per_interval(tracker, clock):
    pose = FakePose()
    monitor = PatientMonitor(tracker, pose, pose_interval_s=2, telemetry=None, sensor_poll_s=None, clock=clock)

    async def frames():
        for _ in range(5):  # 5 frames within 1 s
            monitor.offer_frame(b"jpeg", None)
            await asyncio.sleep(0)
            clock.now += 0.2
        clock.now += 2
        monitor.offer_frame(b"jpeg", None)
        await asyncio.sleep(0)

    asyncio.run(frames())
    assert pose.calls == 2
    assert tracker.condition().camera.person_in_view


def test_attention_is_logged_once_not_every_refresh(tracker, clock):
    monitor = PatientMonitor(tracker, None, None, None, None, clock=clock)
    tracker.observe(seen(person()))
    clock.now += 601
    tracker.observe(seen(person()))
    for _ in range(3):
        monitor.condition()
        clock.now += 60
        tracker.observe(seen(person()))
    notes = [e.text for e in monitor.events if e.kind == "attention"]
    assert len([n for n in notes if "No movement" in n]) == 1


# ---------------------------------------------------------------- app + dashboard

POSE_ENV = dict(YOLO_MODEL="data/models/test.pt", YOLO_CONFIDENCE="0.35", SCENE_MAX_AGE_S="60",
                YOLO_POSE_MODEL="data/models/test-pose.pt", POSE_INTERVAL_S="1", STILL_ATTENTION_MIN="10",
                AWAY_ATTENTION_MIN="5")


@pytest.mark.parametrize("missing", ["POSE_INTERVAL_S", "STILL_ATTENTION_MIN", "AWAY_ATTENTION_MIN"])
def test_pose_model_requires_its_settings(env, missing):
    env.update(POSE_ENV)
    del env[missing]
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


def test_pose_needs_detection(env):
    env.update(YOLO_POSE_MODEL="data/models/test-pose.pt", POSE_INTERVAL_S="1", STILL_ATTENTION_MIN="10",
               AWAY_ATTENTION_MIN="5")
    with pytest.raises(ConfigError, match="needs YOLO_MODEL"):
        load_settings(env)


@pytest.fixture
def http(env, mock_client, caregiver):
    env.update(POSE_ENV)
    app = create_app(load_settings(env), controller=mock_client, caregiver=caregiver, detector=FakeDetector(),
                     pose=FakePose())
    with TestClient(app) as client:
        yield client


def test_dashboard_shows_live_frame_condition_and_events(http):
    assert http.get("/dashboard/frame.jpg").status_code == 404
    http.post("/vision/detect", files={"image": ("live.jpg", b"jpeg bytes", "image/jpeg")})
    http.post("/assistant/text", json={"text": "tell my son I am hungry"})
    http.post("/emergency")

    state = http.get("/dashboard/state").json()
    assert state["scene"]["detections"][0]["label"] == "cell phone"
    assert state["condition"]["camera"]["person_in_view"] is True
    assert state["condition"]["camera"]["posture"] == "upright"
    kinds = [e["kind"] for e in state["events"]]
    assert kinds[:3] == ["emergency_alert", "command", "message"]  # newest first
    assert state["communication_backend"] == "MOCK"

    frame = http.get("/dashboard/frame.jpg")
    assert frame.content == b"jpeg bytes" and frame.headers["content-type"] == "image/jpeg"
    assert "Caregiver dashboard" in http.get("/dashboard").text


def test_failed_message_is_logged_as_failed(http, channel):
    channel.fail = True
    http.post("/assistant/text", json={"text": "tell my son I am hungry"})
    message = next(e for e in http.get("/dashboard/state").json()["events"] if e["kind"] == "message")
    assert not message["ok"] and "FAILED" in message["text"]


# ---------------------------------------------------------------- password

def basic(password: str, user: str = "caregiver") -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_password_protects_every_page(env, mock_client):
    env["ACCESS_PASSWORD"] = "bed-side-42"
    with TestClient(create_app(load_settings(env), controller=mock_client)) as http:
        for path in ("/phone", "/dashboard", "/dashboard/state", "/health", "/docs"):
            response = http.get(path)
            assert response.status_code == 401 and "Basic" in response.headers["www-authenticate"]
        assert http.post("/arm/stop").status_code == 401
        assert http.get("/health", headers=basic("wrong")).status_code == 401
        assert http.get("/health", headers=basic("bed-side-42")).status_code == 200
        assert http.get("/health", headers=basic("bed-side-42", user="anyone")).status_code == 200
    assert "bed-side-42" not in repr(load_settings(env))
