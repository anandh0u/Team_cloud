"""Turns an Intent into hardware/communication actions and a spoken reply.

Rules that hold for every action:
- The arm only ever receives NAMED poses from config/poses.json.
- Replies describe what actually happened; a failed step is never reported as done.
- Object locations come only from the camera. Without a camera result we say we can't see.
"""
from __future__ import annotations

from typing import Any, Protocol

from app.assistant.emergency import EmergencyHandler
from app.assistant.intent import IntentParser
from app.assistant.responses import Responses
from app.communication.caregiver import CaregiverService
from app.hardware.arm import ArmService
from app.hardware.telemetry import TelemetryService
from app.models import Action, ArmActionResult, AssistantResponse, Intent, ObjectLocation
from app.utils.logger import get_logger

logger = get_logger(__name__)

RESPONSE_KEYS = (
    "stop_done", "stop_failed", "home_done", "find_no_camera", "object_unknown", "get_done",
    "get_no_pose", "call_started", "message_sent", "contact_unknown", "comm_failed",
    "status_unavailable", "status_arm", "status_arm_stopped", "status_pulse", "status_no_pulse", "unknown",
    "get_done_seen", "get_not_seen", "get_no_photo", "release_done", "get_not_calibrated",
)


class ObjectLocator(Protocol):
    """Implemented by the vision layer (phone camera + YOLO, app/vision/locator.py)."""

    async def describe_location(self, obj: str) -> ObjectLocation: ...


class AssistantRouter:
    def __init__(self, parser: IntentParser, arm: ArmService, telemetry: TelemetryService,
                 caregiver: CaregiverService, emergency: EmergencyHandler, responses: Responses,
                 locator: ObjectLocator | None = None):
        responses.require(RESPONSE_KEYS)
        self.parser = parser
        self._arm = arm
        self._telemetry = telemetry
        self._caregiver = caregiver
        self._emergency = emergency
        self._r = responses
        self.locator = locator

    async def handle_text(self, text: str) -> AssistantResponse:
        return await self.execute(self.parser.parse(text))

    async def execute(self, intent: Intent) -> AssistantResponse:
        handler = {
            Action.EMERGENCY: self._emergency_action,
            Action.STOP: self._stop,
            Action.HOME: self._home,
            Action.RELEASE: self._release,
            Action.STATUS: self._status,
            Action.FIND_OBJECT: self._find,
            Action.GET_OBJECT: self._get,
            Action.CALL_CONTACT: self._call,
            Action.MESSAGE_CONTACT: self._message,
        }.get(intent.action)
        if handler is None:
            return self._reply(intent, False, self._r.get("unknown"))
        result = await handler(intent)
        logger.info("assistant %s ok=%s response=%r", intent.action.value, result.ok, result.response)
        return result

    def _reply(self, intent: Intent, ok: bool, response: str, **details: Any) -> AssistantResponse:
        return AssistantResponse(action=intent.action, ok=ok, response=response, object=intent.object,
                                 contact=intent.contact, message=intent.message,
                                 details={"matched": intent.matched, **details})

    # ------------------------------------------------------------------ safety

    async def _emergency_action(self, intent: Intent) -> AssistantResponse:
        result = await self._emergency.trigger(source="voice", text=intent.text)
        details: dict[str, Any] = {"emergency": result.model_dump(mode="json")}
        # "Tell my daughter I need help": alert the caregiver AND pass the message on.
        if intent.contact and intent.message and intent.contact != self._caregiver.emergency_contact:
            details["message_delivery"] = (await self._caregiver.send_message(intent.contact, intent.message)).model_dump()
        return self._reply(intent, result.caregiver_alerted, result.response, **details)

    async def _stop(self, intent: Intent) -> AssistantResponse:
        res = await self._arm.stop()
        return self._reply(intent, res.ok, self._r.get("stop_done" if res.ok else "stop_failed"),
                           arm=res.model_dump(mode="json"))

    async def _home(self, intent: Intent) -> AssistantResponse:
        res = await self._arm.home()
        return self._reply(intent, res.ok, self._r.get("home_done") if res.ok else res.message,
                           arm=res.model_dump(mode="json"))

    async def _release(self, intent: Intent) -> AssistantResponse:
        res = await self._arm.gripper_open()
        return self._reply(intent, res.ok, self._r.get("release_done") if res.ok else res.message,
                           arm=res.model_dump(mode="json"))

    # ------------------------------------------------------------------ objects

    async def _find(self, intent: Intent) -> AssistantResponse:
        if intent.object is None:
            return self._reply(intent, False, self._r.get("object_unknown"))
        if self.locator is None:
            return self._reply(intent, False, self._r.get("find_no_camera", object=intent.object), camera="none")
        location = await self.locator.describe_location(intent.object)
        return self._reply(intent, location.seen, location.response, camera=location.model_dump(mode="json"))

    async def _get(self, intent: Intent) -> AssistantResponse:
        if intent.object is None:
            return self._reply(intent, False, self._r.get("object_unknown"))
        pose = self._arm.poses.pose_for_object(intent.object)
        if pose is None:
            return self._reply(intent, False, self._r.get("get_no_pose", object=intent.object))

        # With a camera, only fetch what it can actually see on the table. Objects the
        # model can't recognise (e.g. medicine) are fetched from their fixed spot unverified.
        location = await self.locator.describe_location(intent.object) if self.locator else None
        if location is not None and location.reason in ("not_seen", "no_photo"):
            key = "get_not_seen" if location.reason == "not_seen" else "get_no_photo"
            return self._reply(intent, False, self._r.get(key, object=intent.object),
                               camera=location.model_dump(mode="json"), camera_verified=False)
        verified = location is not None and location.seen

        # Placeholder angles could swing the arm into something: only use calibrated poses.
        if await self._arm.pose_calibrated(pose) is False:
            return self._reply(intent, False, self._r.get("get_not_calibrated", object=intent.object),
                               camera_verified=verified)

        # Fixed sequence, no free-form grasping: open -> object pose -> close -> hand to user.
        steps: list[ArmActionResult] = []
        for step in (self._arm.gripper_open, lambda: self._arm.move_to_pose(pose), self._arm.gripper_close,
                     lambda: self._arm.move_to_pose(self._arm.poses.return_pose)):
            res = await step()
            steps.append(res)
            if not res.ok:
                return self._reply(intent, False, res.message, steps=[s.model_dump(mode="json") for s in steps],
                                   camera_verified=verified)
        done = (self._r.get("get_done_seen", object=intent.object,
                            position=self._r.get(f"position_{location.position}"))
                if verified else self._r.get("get_done", object=intent.object))
        return self._reply(intent, True, done, steps=[s.model_dump(mode="json") for s in steps],
                           camera_verified=verified,
                           camera=location.model_dump(mode="json") if location else None)

    # ------------------------------------------------------------------ people

    async def _call(self, intent: Intent) -> AssistantResponse:
        if intent.contact is None:
            return self._reply(intent, False, self._r.get("contact_unknown"))
        res = await self._caregiver.call_contact(intent.contact)
        key = "call_started" if res.ok else "comm_failed"
        return self._reply(intent, res.ok, self._r.get(key, contact=intent.contact), delivery=res.model_dump())

    async def _message(self, intent: Intent) -> AssistantResponse:
        if intent.contact is None:
            return self._reply(intent, False, self._r.get("contact_unknown"))
        res = await self._caregiver.send_message(intent.contact, intent.message or "")
        key = "message_sent" if res.ok else "comm_failed"
        return self._reply(intent, res.ok, self._r.get(key, contact=intent.contact), delivery=res.model_dump())

    # ------------------------------------------------------------------ status

    async def _status(self, intent: Intent) -> AssistantResponse:
        snap = await self._telemetry.snapshot()
        if not snap.available:
            return self._reply(intent, False, self._r.get("status_unavailable"),
                               telemetry=snap.model_dump(mode="json"))
        parts = []
        if snap.emergency_stop:
            parts.append(self._r.get("status_arm_stopped"))
        elif snap.arm and snap.arm.get("pose"):
            parts.append(self._r.get("status_arm", pose=str(snap.arm["pose"]).lower()))
        if snap.heartbeat and snap.heartbeat.bpm_estimate is not None:
            parts.append(self._r.get("status_pulse", bpm=str(round(snap.heartbeat.bpm_estimate))))
        else:
            parts.append(self._r.get("status_no_pulse"))
        return self._reply(intent, True, " ".join(parts), telemetry=snap.model_dump(mode="json"))
