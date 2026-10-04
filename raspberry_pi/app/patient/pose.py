"""YOLO pose model: finds people and their 17 body keypoints in a camera frame."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Protocol

from app.models import PersonPose, PoseObservation
from app.utils.logger import get_logger
from app.vision.detector import decode_image

logger = get_logger(__name__)


class PoseEstimator(Protocol):
    async def observe(self, image: bytes) -> PoseObservation: ...


class YoloPoseEstimator:
    def __init__(self, model_path: Path, confidence: float):
        from ultralytics import YOLO  # heavy import, deferred on purpose

        model_path.parent.mkdir(parents=True, exist_ok=True)
        self._model = YOLO(str(model_path))  # a missing official model is downloaded
        self._confidence = confidence
        self._lock = asyncio.Lock()
        logger.info("pose: loaded %s", model_path.name)

    def _run(self, image: bytes) -> PoseObservation:
        picture = decode_image(image)
        started = time.perf_counter()
        result = self._model.predict(picture, conf=self._confidence, verbose=False)[0]
        people = []
        if result.keypoints is not None and len(result.boxes):
            for box, conf, kps in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist(),
                                      result.keypoints.data.tolist()):
                people.append(PersonPose(confidence=round(float(conf), 3),
                                         box=tuple(round(float(v), 1) for v in box),
                                         keypoints=[(round(x, 1), round(y, 1), round(c, 3)) for x, y, c in kps]))
        logger.debug("pose: %d people in %.0f ms", len(people), (time.perf_counter() - started) * 1000)
        return PoseObservation(image_width=picture.width, image_height=picture.height, people=people)

    async def observe(self, image: bytes) -> PoseObservation:
        async with self._lock:
            return await asyncio.to_thread(self._run, image)
