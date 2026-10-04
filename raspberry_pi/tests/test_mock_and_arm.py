import asyncio
import random

from app.hardware.arm import ArmService
from app.hardware.mock_controller import MockEsp32Client
from app.hardware.telemetry import TelemetryService
from app.models import ControllerResult, DataSource


def run(coro):
    return asyncio.run(coro)


def test_mock_telemetry_is_tagged_and_complete(mock_client):
    snap = run(TelemetryService(mock_client).snapshot())
    assert snap.available and snap.source is DataSource.MOCK
    assert snap.heartbeat is not None and snap.imu is not None
    assert snap.gripper == "OPEN" and snap.emergency_stop is False and snap.arm is not None
    assert snap.errors == []


def test_mock_replies_match_firmware_contract(mock_client):
    assert run(mock_client.arm_pose("MEDICINE")).data == {"ok": True, "pose": "MEDICINE"}
    assert run(mock_client.gripper_close()).data == {"ok": True, "gripper": "CLOSED"}
    assert run(mock_client.stop()).data == {"ok": True, "emergency_stop": True}
    snap = run(TelemetryService(mock_client).snapshot())
    assert snap.emergency_stop is True and snap.gripper == "CLOSED"


def test_mock_heartbeat_stays_in_configured_range(mock_client, mock_catalog):
    hb = mock_catalog["heartbeat"]
    for _ in range(50):
        bpm = run(mock_client.heartbeat()).data["bpm_estimate"]
        assert hb["bpm_center"] - hb["bpm_jitter"] <= bpm <= hb["bpm_center"] + hb["bpm_jitter"]


def test_stop_blocks_motion_until_resume(mock_client, poses, responses):
    arm = ArmService(mock_client, poses, responses)
    assert run(arm.stop()).ok
    blocked = run(arm.move_to_pose(poses.return_pose))
    assert not blocked.ok and blocked.message == responses.get("arm_command_rejected")
    assert not run(arm.gripper_close()).ok
    assert run(arm.resume()).ok
    assert run(arm.move_to_pose(poses.return_pose)).ok
    assert mock_client.pose == poses.return_pose


def test_unknown_pose_never_reaches_controller(poses, responses):
    class Exploding:
        source = DataSource.MOCK

        async def arm_pose(self, pose):
            raise AssertionError("controller must not be called")

    res = run(ArmService(Exploding(), poses, responses).move_to_pose("JUMP"))
    assert not res.ok and res.controller is None
    assert res.message == responses.get("arm_pose_not_allowed")


def test_unavailable_controller_gives_unavailable_message(mock_catalog, poses, responses):
    mock_catalog["controller_available"] = False
    client = MockEsp32Client(mock_catalog, poses, rng=random.Random(0))
    res = run(ArmService(client, poses, responses).home())
    assert not res.ok and res.message == responses.get("arm_unavailable")


def test_unavailable_telemetry_has_no_values(mock_catalog, poses):
    mock_catalog["controller_available"] = False
    client = MockEsp32Client(mock_catalog, poses, rng=random.Random(0))
    snap = run(TelemetryService(client).snapshot())
    assert not snap.available
    assert snap.heartbeat is None and snap.imu is None and snap.emergency_stop is None
    assert snap.errors


def test_bad_sections_are_dropped_not_guessed():
    class Partial:
        source = DataSource.DEVICE

        async def telemetry(self):
            return ControllerResult(ok=True, endpoint="GET /telemetry", source=self.source, status_code=200,
                                    data={"heartbeat": {"bpm": 70}, "gripper": "OPEN", "emergency_stop": "no"})

    snap = run(TelemetryService(Partial()).snapshot())
    assert snap.available and snap.gripper == "OPEN"
    assert snap.heartbeat is None and snap.imu is None and snap.arm is None and snap.emergency_stop is None
    assert snap.errors == ["heartbeat: unexpected format", "imu: missing", "arm: missing",
                           "emergency_stop: unexpected format"]


def test_object_to_pose_mapping(poses):
    for obj, pose in poses.object_to_pose.items():
        assert poses.pose_for_object(obj.upper()) == pose
    assert poses.pose_for_object("television") is None


def test_imu_without_gravity_is_dropped_as_implausible():
    # MPU6050 clones (chip id 0x70) can return a dead accelerometer: all zeros.
    import asyncio
    from app.hardware.telemetry import TelemetryService
    from app.models import ControllerResult, DataSource

    class Fake:
        async def telemetry(self):
            return ControllerResult(ok=True, endpoint="GET /telemetry", source=DataSource.DEVICE, data={
                "heartbeat": {"available": False},
                "imu": {"available": True, "ax": 0, "ay": 0.002, "az": 0.005, "gx": 0.12, "gy": 0.15, "gz": 0.18,
                        "movement_score": 9.81},
                "arm": {"pose": "HOME", "moving": False}, "gripper": "OPEN", "emergency_stop": False})

    snap = asyncio.run(TelemetryService(Fake()).snapshot())
    assert snap.imu is None and any("implausible" in e for e in snap.errors)
