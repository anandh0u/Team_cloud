"""Live patient monitoring for the caregiver dashboard: latest camera frame, pose-based
condition, ESP32 sensor polling and a log of recent events. Everything is in memory."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone

from app.hardware.telemetry import TelemetryService
from app.models import DashboardEvent, DetectionResult, PatientCondition
from app.patient.condition import ConditionTracker
from app.patient.history import ActivityHistory
from app.patient.pose import PoseEstimator
from app.utils.logger import get_logger
from app.vision.detector import InvalidImageError

logger = get_logger(__name__)

MAX_EVENTS = 100


class PatientMonitor:
    def __init__(self, tracker: ConditionTracker, pose: PoseEstimator | None, pose_interval_s: float | None,
                 telemetry: TelemetryService | None, sensor_poll_s: float | None,
                 clock: Callable[[], float] = time.monotonic, history: ActivityHistory | None = None):
        self.tracker = tracker
        self._pose = pose
        self._pose_interval_s = pose_interval_s or 0
        self._telemetry = telemetry
        self._sensor_poll_s = sensor_poll_s
        self._clock = clock
        self.history = history

        self.frame: bytes | None = None
        self.scene: DetectionResult | None = None
        self.frame_at: datetime | None = None
        self._last_pose_at: float | None = None
        self._pose_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self.events: deque[DashboardEvent] = deque(maxlen=MAX_EVENTS)
        self._attention: set[str] = set()

    # ------------------------------------------------------------------ events

    def log(self, kind: str, ok: bool, text: str) -> None:
        self.events.appendleft(DashboardEvent(timestamp=datetime.now(timezone.utc), kind=kind, ok=ok, text=text))

    def condition(self) -> PatientCondition:
        condition = self.tracker.condition()
        # A note goes into the event log once when it appears, not on every refresh
        # (its text changes as the minutes count up, its key doesn't).
        for key, note in zip(condition.attention_keys, condition.attention):
            if key not in self._attention:
                self.log("attention", False, note)
        self._attention = set(condition.attention_keys)
        return condition

    # ------------------------------------------------------------------ camera

    def offer_frame(self, image: bytes, scene: DetectionResult | None) -> None:
        """Called for every analysed camera frame. Pose runs in the background, at most
        once per POSE_INTERVAL_S and never two at a time, so detection stays fast."""
        self.frame, self.scene, self.frame_at = image, scene, datetime.now(timezone.utc)
        if self._pose is None:
            return
        now = self._clock()
        busy = self._pose_task is not None and not self._pose_task.done()
        if busy or (self._last_pose_at is not None and now - self._last_pose_at < self._pose_interval_s):
            return
        self._last_pose_at = now
        self._pose_task = asyncio.create_task(self._run_pose(image))

    async def _run_pose(self, image: bytes) -> None:
        try:
            sample = self.tracker.observe(await self._pose.observe(image))
            if self.history is not None:
                await self.history.record(sample)
        except InvalidImageError:
            pass
        except Exception:  # a pose failure must never take down the live camera
            logger.exception("pose analysis failed")

    # ------------------------------------------------------------------ sensors

    def start(self) -> None:
        if self._telemetry is not None and self._sensor_poll_s:
            self._poll_task = asyncio.create_task(self._poll_sensors())

    async def _poll_sensors(self) -> None:
        while True:
            try:
                self.tracker.update_sensors(await self._telemetry.snapshot())
            except Exception:
                logger.exception("sensor poll failed")
            await asyncio.sleep(self._sensor_poll_s)

    async def stop(self) -> None:
        for task in (self._poll_task, self._pose_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
