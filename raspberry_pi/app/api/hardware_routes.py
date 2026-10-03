from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.models import ArmActionResult, ControllerResult, TelemetrySnapshot

router = APIRouter(tags=["hardware"])


class PoseRequest(BaseModel):
    pose: str = Field(min_length=1, description="Named pose from config/poses.json, e.g. MEDICINE")


class StatusResponse(BaseModel):
    mock_hardware: bool
    allowed_poses: list[str]
    controller: ControllerResult


def _status_code(result: ArmActionResult) -> int:
    if result.ok:
        return 200
    if result.controller is None:
        return 422  # rejected locally, never sent (e.g. unknown pose)
    if result.controller.status_code is None:
        return 503  # ESP32 unreachable / timed out
    return 409  # ESP32 answered but refused (e.g. emergency stop active)


def _arm_response(result: ArmActionResult, response: Response) -> ArmActionResult:
    response.status_code = _status_code(result)
    return result


@router.get("/status", response_model=StatusResponse)
async def status(request: Request) -> Any:
    state = request.app.state
    return StatusResponse(mock_hardware=state.settings.mock_hardware, allowed_poses=state.arm.poses.poses,
                          controller=await state.controller.status())


@router.get("/telemetry", response_model=TelemetrySnapshot)
async def telemetry(request: Request) -> TelemetrySnapshot:
    return await request.app.state.telemetry.snapshot()


@router.post("/arm/pose", response_model=ArmActionResult)
async def arm_pose(body: PoseRequest, request: Request, response: Response) -> ArmActionResult:
    return _arm_response(await request.app.state.arm.move_to_pose(body.pose), response)


@router.post("/arm/home", response_model=ArmActionResult)
async def arm_home(request: Request, response: Response) -> ArmActionResult:
    return _arm_response(await request.app.state.arm.home(), response)


@router.post("/arm/stop", response_model=ArmActionResult)
async def arm_stop(request: Request, response: Response) -> ArmActionResult:
    return _arm_response(await request.app.state.arm.stop(), response)


@router.post("/arm/resume", response_model=ArmActionResult)
async def arm_resume(request: Request, response: Response) -> ArmActionResult:
    """Explicitly re-enables motion after a stop. Never called automatically."""
    return _arm_response(await request.app.state.arm.resume(), response)
