"""Simulated ESP32 controller for MOCK_HARDWARE=true.

Mirrors the real API's shape and its safety behaviour (unknown poses rejected,
no motion while stopped). All readings are synthetic and tagged source=MOCK.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

from app.catalog import require_keys
from app.hardware.poses import PoseCatalog
from app.models import ControllerResult, DataSource
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MockEsp32Client:
    source = DataSource.MOCK

    def __init__(self, catalog: dict[str, Any], poses: PoseCatalog, rng: random.Random | None = None):
        require_keys(catalog, ["controller_available", "latency_s", "move_duration_s", "heartbeat", "imu"],
                     "mock_hardware.json")
        require_keys(catalog["heartbeat"], ["bpm_center", "bpm_jitter", "raw_min", "raw_max"],
                     "mock_hardware.json heartbeat")
        require_keys(catalog["imu"], ["gravity_ms2", "accel_noise_ms2", "gyro_noise_dps", "movement_score_max"],
                     "mock_hardware.json imu")

        self.available: bool = catalog["controller_available"]
        self._latency_s = catalog["latency_s"]
        self._move_s = catalog["move_duration_s"]
        self._hb = catalog["heartbeat"]
        self._imu = catalog["imu"]
        self._poses = poses
        self._rng = rng or random.Random()

        self.pose = poses.home_pose
        self.gripper = "OPEN"
        self.emergency_stop = False

    async def _reply(self, endpoint: str, data: dict[str, Any] | None = None, *,
                     rejected: str | None = None, delay_s: float = 0.0) -> ControllerResult:
        await asyncio.sleep(self._latency_s + delay_s)
        if not self.available:
            logger.error("mock esp32 %s -> unavailable (simulated)", endpoint)
            return ControllerResult(ok=False, endpoint=endpoint, source=self.source,
                                    error="unreachable: simulated outage")
        latency = round(self._latency_s * 1000, 1)
        if rejected:
            # Same shape the firmware uses: HTTP 200 with {"ok": false}.
            logger.warning("mock esp32 %s -> refused: %s", endpoint, rejected)
            return ControllerResult(ok=False, endpoint=endpoint, source=self.source, status_code=200,
                                    data={"ok": False, "error": rejected},
                                    error=f"controller refused: {rejected}", latency_ms=latency)
        logger.info("mock esp32 %s -> 200", endpoint)
        return ControllerResult(ok=True, endpoint=endpoint, source=self.source, status_code=200,
                                data=data, latency_ms=latency)

    def _status(self) -> dict[str, Any]:
        return {"arm": {"pose": self.pose, "moving": False}, "gripper": self.gripper,
                "emergency_stop": self.emergency_stop, "mock": True}

    def _heartbeat(self) -> dict[str, Any]:
        bpm = self._hb["bpm_center"] + self._rng.uniform(-self._hb["bpm_jitter"], self._hb["bpm_jitter"])
        return {"available": True, "raw": self._rng.randint(self._hb["raw_min"], self._hb["raw_max"]),
                "bpm_estimate": round(bpm, 1)}

    def _imu_sample(self) -> dict[str, Any]:
        a, g = self._imu["accel_noise_ms2"], self._imu["gyro_noise_dps"]
        gauss = self._rng.gauss
        return {
            "available": True,
            "ax": round(gauss(0, a), 3), "ay": round(gauss(0, a), 3),
            "az": round(self._imu["gravity_ms2"] + gauss(0, a), 3),
            "gx": round(gauss(0, g), 3), "gy": round(gauss(0, g), 3), "gz": round(gauss(0, g), 3),
            "movement_score": round(self._rng.uniform(0, self._imu["movement_score_max"]), 3),
        }

    async def health(self) -> ControllerResult:
        return await self._reply("GET /health", {"status": "ok", "mock": True})

    async def status(self) -> ControllerResult:
        return await self._reply("GET /status", self._status())

    async def telemetry(self) -> ControllerResult:
        return await self._reply("GET /telemetry",
                                 {"heartbeat": self._heartbeat(), "imu": self._imu_sample(), **self._status()})

    async def heartbeat(self) -> ControllerResult:
        return await self._reply("GET /heartbeat", self._heartbeat())

    async def imu(self) -> ControllerResult:
        return await self._reply("GET /imu", self._imu_sample())

    async def _move(self, endpoint: str, pose: str) -> ControllerResult:
        if self.emergency_stop:
            return await self._reply(endpoint, rejected="emergency stop active; call /resume first")
        if not self._poses.is_allowed(pose):
            return await self._reply(endpoint, rejected=f"unknown pose {pose}")
        result = await self._reply(endpoint, {"ok": True, "pose": pose.upper()}, delay_s=self._move_s)
        if result.ok:
            self.pose = pose.upper()
        return result

    async def arm_home(self) -> ControllerResult:
        return await self._move("POST /arm/home", self._poses.home_pose)

    async def arm_pose(self, pose: str) -> ControllerResult:
        return await self._move("POST /arm/pose", pose)

    async def _grip(self, endpoint: str, state: str) -> ControllerResult:
        if self.emergency_stop:
            return await self._reply(endpoint, rejected="emergency stop active; call /resume first")
        result = await self._reply(endpoint, {"ok": True, "gripper": state}, delay_s=self._move_s)
        if result.ok:
            self.gripper = state
        return result

    async def gripper_open(self) -> ControllerResult:
        return await self._grip("POST /gripper/open", "OPEN")

    async def gripper_close(self) -> ControllerResult:
        return await self._grip("POST /gripper/close", "CLOSED")

    async def stop(self) -> ControllerResult:
        result = await self._reply("POST /stop", {"ok": True, "emergency_stop": True})
        if result.ok:
            self.emergency_stop = True
        return result

    async def resume(self) -> ControllerResult:
        result = await self._reply("POST /resume", {"ok": True, "emergency_stop": False})
        if result.ok:
            self.emergency_stop = False
        return result

    async def aclose(self) -> None:
        return None
