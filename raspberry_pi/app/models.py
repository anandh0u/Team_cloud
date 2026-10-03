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
