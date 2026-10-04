from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.config import PROJECT_ROOT
from app.models import DashboardEvent, DetectionResult, PatientCondition

router = APIRouter(tags=["dashboard"])

DASHBOARD_PAGE = PROJECT_ROOT / "app" / "web" / "dashboard.html"


class DashboardState(BaseModel):
    condition: PatientCondition
    frame_at: datetime | None  # when the latest camera frame arrived (UTC)
    scene: DetectionResult | None
    events: list[DashboardEvent]
    communication_backend: str
    mock_hardware: bool


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page() -> HTMLResponse:
    """Caregiver dashboard: patient condition, live camera view, recent requests and alerts."""
    return HTMLResponse(DASHBOARD_PAGE.read_text(encoding="utf-8"))


@router.get("/dashboard/state", response_model=DashboardState)
async def dashboard_state(request: Request) -> DashboardState:
    state = request.app.state
    monitor = state.monitor
    return DashboardState(condition=monitor.condition(), frame_at=monitor.frame_at, scene=monitor.scene,
                          events=list(monitor.events), communication_backend=state.caregiver.backend,
                          mock_hardware=state.settings.mock_hardware)


@router.get("/dashboard/frame.jpg", include_in_schema=False)
async def dashboard_frame(request: Request) -> Response:
    frame = request.app.state.monitor.frame
    if frame is None:
        raise HTTPException(404, "No camera frame yet")
    return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
