"""Reads telemetry from the controller's GET /telemetry:

    {"heartbeat": {"available": true, "raw": 520, "bpm_estimate": 76},
     "imu": {"available": true, "ax": 0.04, ..., "az": 9.74, ..., "movement_score": 0.27},
     "arm": {"pose": "HOME", "moving": false}, "gripper": "OPEN", "emergency_stop": false}

Each section is parsed on its own: a missing or malformed section becomes None
and is listed in `errors`, so one bad sensor doesn't hide the others. If the
controller doesn't answer, the snapshot is unavailable with no values at all.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ValidationError

from app.hardware.esp32_client import ControllerClient
from app.models import HeartbeatReading, ImuReading, TelemetrySnapshot
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _section(data: dict[str, Any], key: str, model: type[BaseModel], errors: list[str]):
    if key not in data or data[key] is None:
        errors.append(f"{key}: missing")
        return None
    # The ESP32 reports a failed sensor as {"available": false} and keeps everything else running.
    if isinstance(data[key], dict) and data[key].get("available") is False:
        errors.append(f"{key}: sensor unavailable")
        return None
    try:
        return model.model_validate(data[key])
    except ValidationError as exc:
        errors.append(f"{key}: unexpected format")
        logger.warning("telemetry %s in unexpected format: %s (%d issue(s))", key, data[key], exc.error_count())
        return None


def _typed(data: dict[str, Any], key: str, kind: type, errors: list[str]):
    value = data.get(key)
    if value is None:
        errors.append(f"{key}: missing")
        return None
    if not isinstance(value, kind):
        errors.append(f"{key}: unexpected format")
        logger.warning("telemetry %s has unexpected type %s", key, type(value).__name__)
        return None
    return value


# A working accelerometer always feels gravity (9.81 m/s^2) plus movement. A total far
# outside this band means the sensor isn't really measuring (seen on MPU6050 clones whose
# accelerometer reads zero), so the reading is dropped rather than shown.
PLAUSIBLE_ACCEL_MS2 = (4.0, 30.0)


def _plausible_imu(imu: ImuReading | None, errors: list[str]) -> ImuReading | None:
    if imu is None:
        return None
    total = math.sqrt(imu.ax ** 2 + imu.ay ** 2 + imu.az ** 2)
    if not PLAUSIBLE_ACCEL_MS2[0] <= total <= PLAUSIBLE_ACCEL_MS2[1]:
        errors.append(f"imu: implausible reading (acceleration {total:.1f} m/s², expected about 9.8)")
        logger.warning("imu reading dropped: total acceleration %.2f m/s^2 is not plausible", total)
        return None
    return imu


class TelemetryService:
    def __init__(self, client: ControllerClient):
        self._client = client

    async def snapshot(self) -> TelemetrySnapshot:
        result = await self._client.telemetry()
        now = datetime.now(timezone.utc)
        if not result.ok:
            return TelemetrySnapshot(available=False, source=result.source, timestamp=now,
                                     errors=[result.error or "controller error"])

        data = result.data or {}
        errors: list[str] = []
        heartbeat = _section(data, "heartbeat", HeartbeatReading, errors)
        imu = _plausible_imu(_section(data, "imu", ImuReading, errors), errors)
        arm = _typed(data, "arm", dict, errors)
        gripper = _typed(data, "gripper", str, errors)
        emergency_stop = _typed(data, "emergency_stop", bool, errors)

        src = result.source.value
        if heartbeat:
            logger.info("heartbeat raw=%d bpm_estimate=%s source=%s", heartbeat.raw, heartbeat.bpm_estimate, src)
        if imu:
            logger.info("imu a=(%.3f, %.3f, %.3f) g=(%.2f, %.2f, %.2f) source=%s",
                        imu.ax, imu.ay, imu.az, imu.gx, imu.gy, imu.gz, src)
        logger.info("arm=%s gripper=%s emergency_stop=%s source=%s", arm, gripper, emergency_stop, src)
        if errors:
            logger.warning("telemetry incomplete: %s", "; ".join(errors))

        return TelemetrySnapshot(available=True, source=result.source, timestamp=now, heartbeat=heartbeat,
                                 imu=imu, arm=arm, gripper=gripper, emergency_stop=emergency_stop,
                                 errors=errors)
