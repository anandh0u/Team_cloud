from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.config import PROJECT_ROOT
from app.models import ActivityReport

router = APIRouter(tags=["report"])

REPORT_PAGE = PROJECT_ROOT / "app" / "web" / "report.html"


@router.get("/report", response_class=HTMLResponse, include_in_schema=False)
async def report_page() -> HTMLResponse:
    """Activity report for the caregiver: trend, positions, 7-day chart, AI summary."""
    return HTMLResponse(REPORT_PAGE.read_text(encoding="utf-8"))


@router.get("/report/data", response_model=ActivityReport)
async def report_data(request: Request, hours: int = Query(24, ge=1, le=168),
                      ai: bool = Query(True, description="add the AI-written summary (OpenAI)")) -> ActivityReport:
    reports = request.app.state.reports
    if reports is None:
        raise HTTPException(503, "Reports are off: set YOLO_POSE_MODEL and DATABASE_PATH in .env")
    return await reports.build(hours, with_ai=ai)
