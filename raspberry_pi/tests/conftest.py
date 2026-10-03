from __future__ import annotations

import random

import pytest

from app.assistant.intent import IntentCatalog, IntentParser
from app.assistant.responses import Responses
from app.communication.caregiver import CaregiverService
from app.communication.mock import MockChannel
from app.catalog import load_json_catalog
from app.config import PROJECT_ROOT, load_settings
from app.hardware.mock_controller import MockEsp32Client
from app.hardware.poses import PoseCatalog

TEST_CONTROLLER_URL = "http://esp32.test"


@pytest.fixture
def env() -> dict[str, str]:
    """Minimal valid environment for tests; individual tests override or drop keys."""
    return {
        "MOCK_HARDWARE": "true",
        "API_HOST": "127.0.0.1",
        "API_PORT": "8000",
        "LOG_LEVEL": "WARNING",
        "ASSISTANT_LANGUAGE": "en",
        "CATALOG_DIR": str(PROJECT_ROOT / "config"),
        "ESP32_HTTP_TIMEOUT_S": "0.5",
        "ESP32_HTTP_RETRIES": "2",
        "ESP32_RETRY_BACKOFF_S": "0",
        "COMMUNICATION_BACKEND": "MOCK",
    }


@pytest.fixture
def settings(env):
    return load_settings(env)


@pytest.fixture
def poses(settings) -> PoseCatalog:
    return PoseCatalog.load(settings.pose_catalog_path)


@pytest.fixture
def responses(settings) -> Responses:
    return Responses.load(settings.responses_path, settings.assistant_language)


@pytest.fixture
def mock_catalog(settings) -> dict:
    catalog = load_json_catalog(settings.mock_catalog_path)
    catalog["latency_s"] = 0
    catalog["move_duration_s"] = 0
    return catalog


@pytest.fixture
def mock_client(mock_catalog, poses) -> MockEsp32Client:
    return MockEsp32Client(mock_catalog, poses, rng=random.Random(0))


@pytest.fixture
def intents(settings) -> IntentCatalog:
    return IntentCatalog.load(settings.intents_path)


@pytest.fixture
def parser(intents) -> IntentParser:
    return IntentParser(intents)


@pytest.fixture
def channel() -> MockChannel:
    return MockChannel()


@pytest.fixture
def caregiver(channel, intents) -> CaregiverService:
    return CaregiverService(channel, intents.emergency_contact)
