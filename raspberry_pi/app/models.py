"""Shared Pydantic models.

Every hardware-derived value carries `source` (DEVICE or MOCK) so simulated
readings can never be mistaken for real ones.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


class DataSource(str, Enum):
    DEVICE = "DEVICE"
    MOCK = "MOCK"


class ControllerResult(BaseModel):
    """Outcome of one call to the ESP32 controller (real or mock)."""
    ok: bool
    endpoint: str
    source: DataSource
    status_code: int | None = None  # None = no HTTP response (timeout, unreachable)
    data: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float | None = None


class HeartbeatReading(BaseModel):
    """Hobby pulse sensor output (ESP32 contract). bpm_estimate is a rough estimate,
    not a clinical measurement."""
    raw: int
    bpm_estimate: float | None = None


class ImuReading(BaseModel):
    """MPU6050 sample (ESP32 contract): acceleration in m/s², rotation in degrees/second."""
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    movement_score: float | None = None


class TelemetrySnapshot(BaseModel):
    """`available` = the controller answered. Each section is None if it was missing or
    malformed (listed in `errors`). Values are never filled in."""
    available: bool
    source: DataSource
    timestamp: datetime
    heartbeat: HeartbeatReading | None = None
    imu: ImuReading | None = None
    arm: dict[str, Any] | None = None
    gripper: str | None = None
    emergency_stop: bool | None = None
    errors: list[str] = []


class ArmActionResult(BaseModel):
    ok: bool
    action: str
    pose: str | None = None
    message: str | None = None  # user-facing phrase when the action did not happen
    controller: ControllerResult | None = None


class Action(str, Enum):
    FIND_OBJECT = "FIND_OBJECT"
    GET_OBJECT = "GET_OBJECT"
    CALL_CONTACT = "CALL_CONTACT"
    MESSAGE_CONTACT = "MESSAGE_CONTACT"
    EMERGENCY = "EMERGENCY"
    STOP = "STOP"
    HOME = "HOME"
    STATUS = "STATUS"
    UNKNOWN = "UNKNOWN"


class Intent(BaseModel):
    action: Action
    object: str | None = None
    contact: str | None = None
    message: str | None = None
    parser: Literal["rules", "llm"] = "rules"
    matched: str | None = None  # the phrase/pattern that triggered this intent
    text: str = ""


class AssistantResponse(BaseModel):
    action: Action
    ok: bool
    response: str  # what is spoken back to the user
    object: str | None = None
    contact: str | None = None
    message: str | None = None
    details: dict[str, Any] = {}


class CommResult(BaseModel):
    ok: bool
    kind: Literal["message", "call", "emergency_alert"]
    contact: str
    backend: str
    error: str | None = None
    call_placed: bool = False  # the bedside phone dialled automatically (no tap needed)


class EmergencyResult(BaseModel):
    arm_stopped: bool
    caregiver_alerted: bool
    response: str
    arm: ArmActionResult
    alert: CommResult


class ComponentHealth(BaseModel):
    name: str
    reachable: bool
    source: DataSource
    latency_ms: float | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    mock_hardware: bool
    components: list[ComponentHealth]


class Detection(BaseModel):
    label: str
    confidence: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in image pixels


class DetectionResult(BaseModel):
    model: str
    image_width: int
    image_height: int
    inference_ms: float
    detections: list[Detection]


class ObjectLocation(BaseModel):
    """What the camera says about one object. `seen` is True only when YOLO found it
    in a recent photo; `response` is the spoken phrase either way."""
    seen: bool
    response: str
    label: str | None = None
    confidence: float | None = None
    position: Literal["left", "middle", "right"] | None = None
    neighbour: str | None = None
    photo_age_s: float | None = None


class PhoneCommandResponse(BaseModel):
    """Result of one command from the bedside phone page. Text, vision and voice parts
    fail independently: a broken camera or speech service never blocks the command."""
    transcript: str | None = None  # what Sarvam heard (None when the command was typed)
    language_code: str | None = None
    assistant: AssistantResponse
    vision: DetectionResult | None = None
    vision_error: str | None = None
    reply_text: str | None = None  # what was spoken: assistant.response, translated if the patient spoke another language
    reply_language: str | None = None
    reply_audio: str | None = None  # base64 WAV of reply_text
    voice_error: str | None = None
    dial: str | None = None  # number for the phone to open in its dialler (calls, emergencies)


class PersonPose(BaseModel):
    confidence: float
    box: tuple[float, float, float, float]
    keypoints: list[tuple[float, float, float]]  # 17 COCO keypoints: x, y, confidence


class PoseObservation(BaseModel):
    image_width: int
    image_height: int
    people: list[PersonPose]


Posture = Literal["lying", "reclined", "upright", "unknown"]


class CameraCondition(BaseModel):
    """What the camera shows about the patient. Observations, never a diagnosis."""
    available: bool  # a frame was analysed recently
    last_frame_age_s: float | None = None
    person_in_view: bool | None = None
    posture: Posture | None = None
    still_for_s: float | None = None  # time since movement was last seen (while in view)
    not_in_view_for_s: float | None = None


class SensorCondition(BaseModel):
    """Latest ESP32 telemetry (hobby sensors, not clinical measurements)."""
    available: bool
    source: DataSource | None = None
    age_s: float | None = None
    heartbeat_bpm: float | None = None
    movement_score: float | None = None
    emergency_stop: bool | None = None
    errors: list[str] = []


class PatientCondition(BaseModel):
    camera: CameraCondition
    sensors: SensorCondition
    attention: list[str]  # plain-language notes for the caregiver
    attention_keys: list[str] = []  # stable ids of those notes, e.g. "attention_still"


class DashboardEvent(BaseModel):
    timestamp: datetime
    kind: Literal["command", "message", "call", "emergency_alert", "attention"]
    ok: bool
    text: str


class ActivitySample(BaseModel):
    """One pose reading, as stored for activity reports."""
    timestamp: float  # Unix time
    in_view: bool
    posture: Posture | None = None
    movement: float | None = None  # keypoint shift relative to body size; None = not comparable
    moved: bool | None = None


class PeriodStats(BaseModel):
    """Activity over one period, from the stored pose readings. Percentages are 0-100."""
    start: datetime
    end: datetime
    readings: int
    observed_min: float  # how long the camera was actually watching
    enough_data: bool
    in_view_pct: float | None = None
    lying_pct: float | None = None
    reclined_pct: float | None = None
    upright_pct: float | None = None
    active_pct: float | None = None  # share of in-view readings with movement
    longest_still_min: float | None = None


class DailyActivity(BaseModel):
    day: str  # YYYY-MM-DD, local time
    observed_min: float
    active_pct: float | None = None


class ActivityReport(BaseModel):
    generated_at: datetime
    hours: int
    current: PeriodStats
    previous: PeriodStats
    trend: Literal["more_active", "less_active", "about_the_same", "not_enough_data"]
    active_change_points: float | None = None
    daily: list[DailyActivity]
    highlights: list[str]  # plain sentences built from the numbers
    ai_summary: str | None = None
    ai_error: str | None = None
