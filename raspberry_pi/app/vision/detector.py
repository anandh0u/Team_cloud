"""YOLO object detection on photos sent by the bedside phone.

ultralytics (and PyTorch) are imported only when a detector is built, so the rest
of the app and the tests run without the vision packages installed.
"""
from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path
from typing import Protocol

from app.models import Detection, DetectionResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


class InvalidImageError(ValueError):
    """The uploaded bytes are not a readable image."""


def decode_image(image: bytes):
    """Bytes -> upright RGB PIL image. Phone photos are often stored sideways with an
    EXIF rotation flag, which exif_transpose applies."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        return ImageOps.exif_transpose(Image.open(io.BytesIO(image))).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError("not a readable image") from exc


class Detector(Protocol):
    async def detect(self, image: bytes) -> DetectionResult: ...


class YoloDetector:
    def __init__(self, model_path: Path, confidence: float):
        from ultralytics import YOLO  # heavy import, deferred on purpose

        model_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        # A missing official model (e.g. yolo11n.pt) is downloaded to model_path.
        self._model = YOLO(str(model_path))
        self._name = model_path.name
        self._confidence = confidence
        # One inference at a time: the Pi's CPU is the bottleneck, and parallel runs
        # would only slow each other down and double the memory use.
        self._lock = asyncio.Lock()
        logger.info("vision: loaded %s in %.1f s (confidence >= %.2f)",
                    self._name, time.perf_counter() - started, confidence)

    def warm_up(self) -> None:
        """Run once on a blank image so the first real photo isn't slowed by lazy init."""
        from PIL import Image

        self._model.predict(Image.new("RGB", (640, 480)), conf=self._confidence, verbose=False)

    def _run(self, image: bytes) -> DetectionResult:
        picture = decode_image(image)
        started = time.perf_counter()
        result = self._model.predict(picture, conf=self._confidence, verbose=False)[0]
        inference_ms = (time.perf_counter() - started) * 1000

        detections = [
            Detection(label=result.names[int(cls)], confidence=round(float(conf), 3),
                      box=tuple(round(float(v), 1) for v in xyxy))
            for xyxy, conf, cls in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist(),
                                       result.boxes.cls.tolist())
        ]
        detections.sort(key=lambda d: d.confidence, reverse=True)
        logger.info("vision: %dx%d in %.0f ms -> %s", picture.width, picture.height, inference_ms,
                    ", ".join(f"{d.label} {d.confidence:.2f}" for d in detections) or "nothing")
        return DetectionResult(model=self._name, image_width=picture.width, image_height=picture.height,
                               inference_ms=round(inference_ms, 1), detections=detections)

    async def detect(self, image: bytes) -> DetectionResult:
        async with self._lock:
            return await asyncio.to_thread(self._run, image)
