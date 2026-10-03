from __future__ import annotations

from fastapi import APIRouter, Request

from app.models import ComponentHealth, HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """API is up; reports whether the ESP32 controller answers. Never fails on hardware errors."""
    state = request.app.state
    result = await state.controller.health()
    components = [ComponentHealth(name="esp32_controller", reachable=result.ok, source=result.source,
                                  latency_ms=result.latency_ms, error=result.error)]
    return HealthResponse(
        status="ok" if all(c.reachable for c in components) else "degraded",
        mock_hardware=state.settings.mock_hardware,
        components=components,
    )
