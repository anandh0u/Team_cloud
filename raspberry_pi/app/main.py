"""FastAPI application factory. Run with `python run.py`."""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (assistant_routes, caregiver_routes, dashboard_routes, hardware_routes, phone_routes,
                     report_routes, system_routes, vision_routes)
from app.api.access import PasswordMiddleware
from app.assistant.emergency import EmergencyHandler
from app.assistant.intent import IntentCatalog, IntentParser
from app.assistant.responses import Responses
from app.assistant.router import AssistantRouter
from app.communication.caregiver import CaregiverService, build_caregiver
from app.config import ConfigError, Settings, load_settings
from app.hardware.arm import ArmService
from app.hardware.esp32_client import ControllerClient, build_controller
from app.hardware.poses import PoseCatalog
from app.hardware.telemetry import TelemetryService
from app.models import CommResult
from app.llm.assistant_llm import AssistantLLM
from app.llm.openai_summary import OpenAISummarizer
from app.patient.condition import ConditionTracker
from app.patient.history import ActivityHistory
from app.patient.monitor import PatientMonitor
from app.patient.pose import PoseEstimator, YoloPoseEstimator
from app.patient.report import ReportBuilder
from app.utils.logger import get_logger, setup_logging
from app.vision.detector import Detector, YoloDetector
from app.vision.locator import SceneLocator, load_object_labels
from app.voice.sarvam import SarvamVoice, Voice

logger = get_logger(__name__)


def create_app(settings: Settings | None = None, controller: ControllerClient | None = None,
               caregiver: CaregiverService | None = None, detector: Detector | None = None,
               voice: Voice | None = None, pose: PoseEstimator | None = None) -> FastAPI:
    settings = settings or load_settings()
    setup_logging(settings.log_level)

    # Load catalogs and build services before serving, so a bad config fails at startup.
    poses = PoseCatalog.load(settings.pose_catalog_path)
    responses = Responses.load(settings.responses_path, settings.assistant_language)
    intents = IntentCatalog.load(settings.intents_path)
    if caregiver is None:
        responses.require(("sms_message", "sms_call_request"))
        sms_texts = {"message": responses.get("sms_message", message="{message}"),
                     "call_request": responses.get("sms_call_request")}
        caregiver = build_caregiver(settings, intents.emergency_contact, sms_texts, intents.contacts)
    if detector is None and settings.yolo_model is not None:
        detector = YoloDetector(settings.yolo_model, settings.yolo_confidence)
    locator = None
    if detector is not None:
        if settings.scene_max_age_s is None:
            raise ConfigError("SCENE_MAX_AGE_S is required when vision is on (see .env.example)")
        locator = SceneLocator(load_object_labels(settings.vision_labels_path, intents.objects), responses,
                               settings.scene_max_age_s)

    if pose is None and settings.yolo_pose_model is not None:
        pose = YoloPoseEstimator(settings.yolo_pose_model, settings.yolo_confidence)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = controller or build_controller(settings, poses)
        speech = voice or (SarvamVoice(settings) if settings.sarvam_api_key else None)
        arm = ArmService(client, poses, responses)
        telemetry = TelemetryService(client)
        emergency = EmergencyHandler(arm, caregiver, responses)

        app.state.settings = settings
        app.state.controller = client
        app.state.arm = arm
        app.state.telemetry = telemetry
        app.state.caregiver = caregiver
        app.state.emergency = emergency
        app.state.detector = detector
        app.state.locator = locator
        app.state.voice = speech
        history = ActivityHistory(settings.database_path, settings.history_days) if settings.database_path else None
        summarizer = OpenAISummarizer(settings) if settings.llm_provider == "openai" else None
        app.state.monitor = monitor = _build_monitor(settings, responses, pose, telemetry, history)
        app.state.reports = ReportBuilder(history, settings.pose_interval_s, summarizer) if history else None
        caregiver.on_result = lambda result: monitor.log(result.kind, result.ok, _describe_comm(result))
        monitor.start()
        if isinstance(detector, YoloDetector):
            await asyncio.to_thread(detector.warm_up)
        assistant_llm = (AssistantLLM(settings, list(intents.objects), list(intents.contacts),
                                      intents.emergency_call_contacts) if settings.llm_assistant else None)
        app.state.assistant = AssistantRouter(IntentParser(intents), arm, telemetry, caregiver, emergency, responses,
                                              locator=locator, llm=assistant_llm)
        logger.info("startup: mock_hardware=%s language=%s controller=%s communication=%s vision=%s voice=%s",
                    settings.mock_hardware, settings.assistant_language, client.source.value, caregiver.backend,
                    "on" if detector else "off", "on" if speech else "off")
        try:
            yield
        finally:
            await monitor.stop()
            if history is not None:
                history.close()
            if summarizer is not None:
                await summarizer.aclose()
            if assistant_llm is not None:
                await assistant_llm.aclose()
            await client.aclose()
            await caregiver.aclose()
            if speech is not None:
                await speech.aclose()
            logger.info("shutdown complete")

    app = FastAPI(title="Assistive bedside system (Raspberry Pi)", lifespan=lifespan)
    if settings.access_password:
        app.add_middleware(PasswordMiddleware, password=settings.access_password)
    else:
        logger.warning("ACCESS_PASSWORD is not set: anyone on the network can use every page. "
                       "Set it before making the server reachable from the internet.")
    for module in (system_routes, hardware_routes, assistant_routes, caregiver_routes, vision_routes, phone_routes,
                   dashboard_routes, report_routes):
        app.include_router(module.router)
    return app


def _build_monitor(settings: Settings, responses: Responses, pose: PoseEstimator | None,
                   telemetry: TelemetryService, history: ActivityHistory | None) -> PatientMonitor:
    clock = time.monotonic
    pose_on = pose is not None and settings.pose_interval_s is not None
    tracker = ConditionTracker(
        responses,
        still_attention_s=settings.still_attention_min * 60 if pose_on else None,
        away_attention_s=settings.away_attention_min * 60 if pose_on else None,
        # A camera that hasn't delivered a pose frame for 5 intervals (min 15 s) counts as offline.
        camera_timeout_s=max(15.0, 5 * settings.pose_interval_s) if pose_on else None,
        sensor_timeout_s=3 * settings.sensor_poll_s if settings.sensor_poll_s else None,
        clock=clock)
    return PatientMonitor(tracker, pose if pose_on else None, settings.pose_interval_s,
                          telemetry, settings.sensor_poll_s, clock=clock, history=history)


def _describe_comm(result: CommResult) -> str:
    what = {"message": "Message", "call": "Call request", "emergency_alert": "Emergency alert"}[result.kind]
    text = f"{what} to {result.contact} via {result.backend}"
    return text if result.ok else f"{text} FAILED ({result.error})"
