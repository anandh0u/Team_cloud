from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, UploadFile

from app.models import DetectionResult
from app.vision.detector import InvalidImageError

router = APIRouter(tags=["vision"])

MAX_IMAGE_BYTES = 15 * 1024 * 1024


@router.post("/vision/detect", response_model=DetectionResult)
async def detect(image: UploadFile, request: Request) -> DetectionResult:
    """Run YOLO on one photo (multipart field `image`). 503 if vision is disabled."""
    detector = request.app.state.detector
    if detector is None:
        raise HTTPException(503, "Vision is disabled: set YOLO_MODEL in .env")
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(400, "Empty image")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"Image larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    try:
        scene = await detector.detect(data)
    except InvalidImageError as exc:
        raise HTTPException(400, str(exc)) from exc
    request.app.state.locator.update(scene)  # so "where is my phone?" can use this photo
    request.app.state.monitor.offer_frame(data, scene)  # live dashboard + patient pose
    return scene
