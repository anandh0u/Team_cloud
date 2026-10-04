"""Arm actions by NAMED pose only. The Pi never computes or sends servo angles."""
from __future__ import annotations

from app.assistant.responses import Responses
from app.hardware.esp32_client import ControllerClient
from app.hardware.poses import PoseCatalog
from app.models import ArmActionResult, ControllerResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

RESPONSE_KEYS = ("arm_unavailable", "arm_command_rejected", "arm_pose_not_allowed")


class ArmService:
    def __init__(self, client: ControllerClient, poses: PoseCatalog, responses: Responses):
        responses.require(RESPONSE_KEYS)
        self._client = client
        self.poses = poses
        self._responses = responses

    def _result(self, action: str, pose: str | None, res: ControllerResult) -> ArmActionResult:
        message = None
        if not res.ok:
            # No HTTP response at all -> the arm is unavailable; an HTTP error -> it refused.
            key = "arm_unavailable" if res.status_code is None else "arm_command_rejected"
            message = self._responses.get(key)
        logger.info("arm %s pose=%s ok=%s error=%s source=%s",
                    action, pose, res.ok, res.error, res.source.value)
        return ArmActionResult(ok=res.ok, action=action, pose=pose, message=message, controller=res)

    async def pose_calibrated(self, pose: str) -> bool | None:
        """Whether the controller has calibrated angles for `pose` (firmware 0.3+ reports this
        in /status). None = unknown (older firmware, simulator, or no answer)."""
        status = await self._client.status()
        calibrated = (status.data or {}).get("calibrated") if status.ok else None
        return calibrated.get(pose.upper()) if isinstance(calibrated, dict) else None

    async def move_to_pose(self, pose: str) -> ArmActionResult:
        pose = pose.strip().upper()
        if not self.poses.is_allowed(pose):
            logger.warning("arm POSE rejected locally: %r is not in the pose catalog", pose)
            return ArmActionResult(ok=False, action="POSE", pose=pose,
                                   message=self._responses.get("arm_pose_not_allowed"))
        return self._result("POSE", pose, await self._client.arm_pose(pose))

    async def home(self) -> ArmActionResult:
        return self._result("HOME", self.poses.home_pose, await self._client.arm_home())

    async def gripper_open(self) -> ArmActionResult:
        return self._result("GRIPPER_OPEN", None, await self._client.gripper_open())

    async def gripper_close(self) -> ArmActionResult:
        return self._result("GRIPPER_CLOSE", None, await self._client.gripper_close())

    async def stop(self) -> ArmActionResult:
        return self._result("STOP", None, await self._client.stop())

    async def resume(self) -> ArmActionResult:
        return self._result("RESUME", None, await self._client.resume())
