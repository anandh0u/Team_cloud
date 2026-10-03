import asyncio

import pytest
from fastapi.testclient import TestClient

from app.assistant.emergency import EmergencyHandler
from app.assistant.router import AssistantRouter
from app.hardware.arm import ArmService
from app.hardware.telemetry import TelemetryService
from app.main import create_app
from app.models import Action


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def router(parser, mock_client, poses, responses, caregiver):
    arm = ArmService(mock_client, poses, responses)
    emergency = EmergencyHandler(arm, caregiver, responses)
    return AssistantRouter(parser, arm, TelemetryService(mock_client), caregiver, emergency, responses)


# ---------------------------------------------------------------- router flows

def test_get_object_runs_fixed_named_pose_sequence(router, mock_client, poses, responses):
    res = run(router.handle_text("I need my medicine"))
    assert res.ok and res.response == responses.get("get_done", object="medicine")
    steps = [(s["action"], s["pose"]) for s in res.details["steps"]]
    assert steps == [("GRIPPER_OPEN", None), ("POSE", "MEDICINE"), ("GRIPPER_CLOSE", None),
                     ("POSE", poses.return_pose)]
    assert mock_client.pose == poses.return_pose and mock_client.gripper == "CLOSED"
    assert res.details["camera_verified"] is False


def test_get_object_stops_at_first_failed_step(router, mock_client, responses):
    run(mock_client.stop())
    res = run(router.handle_text("I need water"))
    assert not res.ok and res.response == responses.get("arm_command_rejected")
    assert len(res.details["steps"]) == 1


def test_get_object_without_pose_does_not_move(router, mock_client, responses):
    res = run(router.handle_text("bring me my remote"))
    assert not res.ok and res.response == responses.get("get_no_pose", object="remote")
    assert mock_client.pose == "HOME"


def test_find_without_camera_never_invents_a_location(router, responses):
    res = run(router.handle_text("Where is my phone?"))
    assert not res.ok and res.response == responses.get("find_no_camera", object="phone")
    assert "right" not in res.response and "left" not in res.response


def test_emergency_stops_arm_and_alerts(router, mock_client, channel, responses):
    res = run(router.handle_text("Help me"))
    assert res.action is Action.EMERGENCY and res.ok
    assert res.response == responses.get("emergency_contacting")
    assert mock_client.emergency_stop is True
    assert channel.sent[0]["kind"] == "message" and channel.sent[0]["contact"] == "caregiver"


def test_emergency_alerts_even_when_arm_unreachable(router, mock_client, channel, responses):
    mock_client.available = False
    res = run(router.handle_text("emergency"))
    assert res.ok and res.response == responses.get("emergency_contacting")
    assert res.details["emergency"]["arm_stopped"] is False
    assert len(channel.sent) == 1


def test_emergency_reply_is_honest_when_alert_fails(router, channel, responses):
    channel.fail = True
    res = run(router.handle_text("I need help"))
    assert not res.ok and res.response == responses.get("emergency_alert_failed")


def test_emergency_also_delivers_message_to_named_contact(router, channel):
    run(router.handle_text("Tell my daughter I need help"))
    assert [(m["contact"], m["message"]) for m in channel.sent][-1] == ("daughter", "I need help")
    assert any(m["contact"] == "caregiver" for m in channel.sent)


def test_stop_reports_failure_truthfully(router, mock_client, responses):
    mock_client.available = False
    res = run(router.handle_text("stop"))
    assert not res.ok and res.response == responses.get("stop_failed")


def test_status_reports_mock_pulse_with_disclaimer(router):
    res = run(router.handle_text("what is my status"))
    assert res.ok and "not a medical measurement" in res.response


def test_status_when_sensors_unreachable(router, mock_client, responses):
    mock_client.available = False
    res = run(router.handle_text("status"))
    assert not res.ok and res.response == responses.get("status_unavailable")


def test_message_and_call_go_through_channel(router, channel):
    assert run(router.handle_text("Call my son")).ok
    assert run(router.handle_text("tell my son I am hungry")).ok
    assert [(m["kind"], m["contact"]) for m in channel.sent] == [("call", "son"), ("message", "son")]
    assert channel.sent[1]["message"] == "I am hungry"


# ---------------------------------------------------------------- HTTP API

@pytest.fixture
def http(settings, mock_client, caregiver):
    with TestClient(create_app(settings, controller=mock_client, caregiver=caregiver)) as client:
        yield client


def test_assistant_text_endpoint(http):
    body = http.post("/assistant/text", json={"text": "Where is my phone?"}).json()
    assert body["action"] == "FIND_OBJECT" and body["object"] == "phone"
    assert body["response"]


def test_arm_routes_and_status_codes(http, mock_client):
    assert http.post("/arm/pose", json={"pose": "MEDICINE"}).status_code == 200
    assert http.post("/arm/pose", json={"pose": "DANCE"}).status_code == 422
    assert http.post("/arm/stop").status_code == 200
    assert http.post("/arm/home").status_code == 409  # stopped: controller refuses
    assert http.post("/arm/resume").status_code == 200
    assert http.post("/arm/home").status_code == 200
    mock_client.available = False
    assert http.post("/arm/home").status_code == 503


def test_status_and_telemetry_routes(http):
    status = http.get("/status").json()
    assert status["mock_hardware"] is True and "MEDICINE" in status["allowed_poses"]
    telemetry = http.get("/telemetry").json()
    assert telemetry["available"] is True and telemetry["source"] == "MOCK"
    assert telemetry["heartbeat"]["bpm_estimate"] is not None


def test_emergency_endpoint(http, mock_client, channel):
    body = http.post("/emergency", json={"source": "phone-button"}).json()
    assert body["arm_stopped"] and body["caregiver_alerted"]
    assert mock_client.emergency_stop and channel.sent


def test_caregiver_routes(http):
    assert http.post("/caregiver/message", json={"contact": "son", "message": "hi"}).status_code == 200
    assert http.post("/caregiver/call", json={"contact": "daughter"}).status_code == 200
    assert http.post("/caregiver/call", json={"contact": "stranger"}).status_code == 422


def test_find_object_endpoint(http):
    body = http.post("/find-object", json={"object": "my mobile"}).json()
    assert body["object"] == "phone" and body["ok"] is False
