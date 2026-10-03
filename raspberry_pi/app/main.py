"""FastAPI application factory. Run with `python run.py`."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import system_routes
from app.assistant.responses import Responses
from app.config import Settings, load_settings
from app.hardware.arm import ArmService
from app.hardware.esp32_client import ControllerClient, build_controller
from app.hardware.poses import PoseCatalog
from app.hardware.telemetry import TelemetryService
from app.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


def create_app(settings: Settings | None = None, controller: ControllerClient | None = None) -> FastAPI:
    settings = settings or load_settings()
    setup_logging(settings.log_level)

    # Load catalogs before serving so a bad config fails at startup, not mid-request.
    poses = PoseCatalog.load(settings.pose_catalog_path)
    responses = Responses.load(settings.responses_path, settings.assistant_language)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = controller or build_controller(settings, poses)
        app.state.settings = settings
        app.state.controller = client
        app.state.arm = ArmService(client, poses, responses)
        app.state.telemetry = TelemetryService(client)
        logger.info("startup: mock_hardware=%s language=%s controller=%s",
                    settings.mock_hardware, settings.assistant_language, client.source.value)
        try:
            yield
        finally:
            await client.aclose()
            logger.info("shutdown complete")

    app = FastAPI(title="Assistive bedside system (Raspberry Pi)", lifespan=lifespan)
    app.include_router(system_routes.router)
    return app
