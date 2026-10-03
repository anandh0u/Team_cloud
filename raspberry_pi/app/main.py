"""FastAPI application factory. Run with `python run.py`."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import assistant_routes, caregiver_routes, hardware_routes, system_routes
from app.assistant.emergency import EmergencyHandler
from app.assistant.intent import IntentCatalog, IntentParser
from app.assistant.responses import Responses
from app.assistant.router import AssistantRouter
from app.communication.caregiver import CaregiverService, build_caregiver
from app.config import Settings, load_settings
from app.hardware.arm import ArmService
from app.hardware.esp32_client import ControllerClient, build_controller
from app.hardware.poses import PoseCatalog
from app.hardware.telemetry import TelemetryService
from app.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


def create_app(settings: Settings | None = None, controller: ControllerClient | None = None,
               caregiver: CaregiverService | None = None) -> FastAPI:
    settings = settings or load_settings()
    setup_logging(settings.log_level)

    # Load catalogs and build services before serving, so a bad config fails at startup.
    poses = PoseCatalog.load(settings.pose_catalog_path)
    responses = Responses.load(settings.responses_path, settings.assistant_language)
    intents = IntentCatalog.load(settings.intents_path)
    caregiver = caregiver or build_caregiver(settings, intents.emergency_contact)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = controller or build_controller(settings, poses)
        arm = ArmService(client, poses, responses)
        telemetry = TelemetryService(client)
        emergency = EmergencyHandler(arm, caregiver, responses)

        app.state.settings = settings
        app.state.controller = client
        app.state.arm = arm
        app.state.telemetry = telemetry
        app.state.caregiver = caregiver
        app.state.emergency = emergency
        app.state.assistant = AssistantRouter(IntentParser(intents), arm, telemetry, caregiver, emergency, responses)
        logger.info("startup: mock_hardware=%s language=%s controller=%s communication=%s",
                    settings.mock_hardware, settings.assistant_language, client.source.value, caregiver.backend)
        try:
            yield
        finally:
            await client.aclose()
            logger.info("shutdown complete")

    app = FastAPI(title="Assistive bedside system (Raspberry Pi)", lifespan=lifespan)
    for module in (system_routes, hardware_routes, assistant_routes, caregiver_routes):
        app.include_router(module.router)
    return app
