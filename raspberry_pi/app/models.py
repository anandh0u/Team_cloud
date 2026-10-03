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
    """Hobby pulse sensor output. bpm is a rough estimate, not a clinical measurement."""
    raw: int
    bpm: float | None = None


class ImuReading(BaseModel):
    """MPU6050 sample: acceleration in g, rotation in degrees/second."""
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float


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
