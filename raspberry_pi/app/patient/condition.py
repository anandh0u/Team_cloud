"""Patient condition from camera pose + ESP32 sensors, for the caregiver dashboard.

Only observations are reported ("lying", "no movement seen for 20 minutes"), never a
medical judgement. Posture comes from the torso angle; movement from how far the body
keypoints shift between pose frames. Both are rough: a camera at an odd angle or a
blanket over the patient makes them less reliable.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable

from app.assistant.responses import Responses
from app.models import (ActivitySample, CameraCondition, PatientCondition, PersonPose, PoseObservation, Posture, SensorCondition,
                        TelemetrySnapshot)

RESPONSE_KEYS = ("attention_still", "attention_away", "attention_camera_off", "attention_sensors_off",
                 "attention_arm_stopped")

# COCO keypoint indices
SHOULDERS, HIPS = (5, 6), (11, 12)
KEYPOINT_MIN_CONFIDENCE = 0.5
PERSON_MIN_CONFIDENCE = 0.5
# Mean keypoint shift, as a fraction of the body box diagonal, that counts as movement.
# Below this is mostly the model's own frame-to-frame jitter.
MOVEMENT_THRESHOLD = 0.03
LYING_DEGREES, UPRIGHT_DEGREES = 60, 30


def _midpoint(kps: list[tuple[float, float, float]], pair: tuple[int, int]) -> tuple[float, float] | None:
    points = [kps[i] for i in pair if i < len(kps) and kps[i][2] >= KEYPOINT_MIN_CONFIDENCE]
    if not points:
        return None
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def posture_of(person: PersonPose) -> Posture:
    """Torso angle from vertical: shoulders-to-hips line upright = sitting/standing, flat = lying."""
    shoulders, hips = _midpoint(person.keypoints, SHOULDERS), _midpoint(person.keypoints, HIPS)
    if shoulders is None or hips is None:
        return "unknown"
    dx, dy = hips[0] - shoulders[0], hips[1] - shoulders[1]
    if math.hypot(dx, dy) < 1:
        return "unknown"
    angle = math.degrees(math.atan2(abs(dx), abs(dy)))
    if angle >= LYING_DEGREES:
        return "lying"
    if angle <= UPRIGHT_DEGREES:
        return "upright"
    return "reclined"


def movement_between(before: PersonPose, after: PersonPose) -> float | None:
    """Mean shift of keypoints seen in both frames, relative to the body size. None if not comparable."""
    x1, y1, x2, y2 = after.box
    diagonal = math.hypot(x2 - x1, y2 - y1)
    shifts = [math.hypot(a[0] - b[0], a[1] - b[1])
              for b, a in zip(before.keypoints, after.keypoints)
              if b[2] >= KEYPOINT_MIN_CONFIDENCE and a[2] >= KEYPOINT_MIN_CONFIDENCE]
    if diagonal < 1 or len(shifts) < 3:
        return None
    return sum(shifts) / len(shifts) / diagonal


def main_person(observation: PoseObservation) -> PersonPose | None:
    """The patient is assumed to be the largest confidently detected person."""
    people = [p for p in observation.people if p.confidence >= PERSON_MIN_CONFIDENCE]
    return max(people, key=lambda p: (p.box[2] - p.box[0]) * (p.box[3] - p.box[1]), default=None)


class ConditionTracker:
    def __init__(self, responses: Responses, still_attention_s: float | None, away_attention_s: float | None,
                 camera_timeout_s: float | None, sensor_timeout_s: float | None, clock: Callable[[], float]):
        """camera_timeout_s=None: camera monitoring is off (no pose model), so no camera notes.
        sensor_timeout_s=None: sensors aren't polled, so no sensor notes."""
        responses.require(RESPONSE_KEYS)
        self._r = responses
        self._still_attention_s = still_attention_s
        self._away_attention_s = away_attention_s
        self._camera_timeout_s = camera_timeout_s
        self._sensor_timeout_s = sensor_timeout_s
        self._clock = clock

        self._last_frame_at: float | None = None
        self._person: PersonPose | None = None
        self._posture: Posture | None = None
        self._last_movement_at: float | None = None
        self._away_since: float | None = None
        self._telemetry: TelemetrySnapshot | None = None
        self._telemetry_at: float | None = None

    def observe(self, observation: PoseObservation, timestamp: float | None = None) -> ActivitySample:
        """Updates the live condition and returns the reading (Unix `timestamp`, default now)
        for the activity history."""
        timestamp = time.time() if timestamp is None else timestamp
        now = self._clock()
        self._last_frame_at = now
        person = main_person(observation)
        if person is None:
            if self._away_since is None:
                self._away_since = now
            self._person = self._posture = None
            return ActivitySample(timestamp=timestamp, in_view=False)
        self._away_since = None
        shift = moved = None
        if self._person is None:
            self._last_movement_at = now  # (re)appearing resets the still timer, but isn't measured movement
        else:
            shift = movement_between(self._person, person)
            moved = shift is not None and shift >= MOVEMENT_THRESHOLD
            if moved:
                self._last_movement_at = now
        self._person = person
        self._posture = posture_of(person)
        return ActivitySample(timestamp=timestamp, in_view=True, posture=self._posture,
                              movement=round(shift, 4) if shift is not None else None,
                              moved=moved if shift is not None else None)

    def update_sensors(self, telemetry: TelemetrySnapshot) -> None:
        self._telemetry = telemetry
        self._telemetry_at = self._clock()

    def _camera(self, now: float) -> CameraCondition:
        if self._camera_timeout_s is None or self._last_frame_at is None:
            return CameraCondition(available=False)
        age = now - self._last_frame_at
        if age > self._camera_timeout_s:
            return CameraCondition(available=False, last_frame_age_s=round(age, 1))
        in_view = self._person is not None
        return CameraCondition(
            available=True, last_frame_age_s=round(age, 1), person_in_view=in_view, posture=self._posture,
            still_for_s=round(now - self._last_movement_at, 1) if in_view and self._last_movement_at else None,
            not_in_view_for_s=None if in_view else round(now - self._away_since, 1))

    def _sensors(self, now: float) -> SensorCondition:
        snap = self._telemetry
        if snap is None or self._telemetry_at is None:
            return SensorCondition(available=False, errors=["not polled yet" if self._sensor_timeout_s else
                                                             "sensor polling is off (SENSOR_POLL_S)"])
        age = now - self._telemetry_at
        fresh = self._sensor_timeout_s is not None and age <= self._sensor_timeout_s
        return SensorCondition(
            available=snap.available and fresh, source=snap.source, age_s=round(age, 1),
            heartbeat_bpm=snap.heartbeat.bpm_estimate if snap.heartbeat else None,
            movement_score=snap.imu.movement_score if snap.imu else None,
            emergency_stop=snap.emergency_stop, errors=snap.errors if fresh else ["reading is out of date"])

    def condition(self) -> PatientCondition:
        now = self._clock()
        camera, sensors = self._camera(now), self._sensors(now)
        notes: dict[str, str] = {}
        if self._camera_timeout_s is None:
            pass
        elif not camera.available:
            notes["attention_camera_off"] = self._r.get("attention_camera_off")
        elif camera.still_for_s is not None and camera.still_for_s >= self._still_attention_s:
            notes["attention_still"] = self._r.get("attention_still", minutes=str(int(camera.still_for_s // 60)))
        elif camera.not_in_view_for_s is not None and camera.not_in_view_for_s >= self._away_attention_s:
            notes["attention_away"] = self._r.get("attention_away",
                                                  minutes=str(int(camera.not_in_view_for_s // 60)))
        if self._sensor_timeout_s is not None and not sensors.available:
            notes["attention_sensors_off"] = self._r.get("attention_sensors_off")
        if sensors.available and sensors.emergency_stop:
            notes["attention_arm_stopped"] = self._r.get("attention_arm_stopped")
        return PatientCondition(camera=camera, sensors=sensors, attention=list(notes.values()),
                                attention_keys=list(notes))
