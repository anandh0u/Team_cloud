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
        "ESP32_MOVE_TIMEOUT_S": "20",
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


# ---------------------------------------------------------------- vision + voice fakes

class FakeDetector:
    """Stands in for YOLO. Returns `scene`; b"not an image" is rejected like a corrupt upload."""

    def __init__(self, scene=None):
        from app.models import Detection, DetectionResult
        self.scene = scene or DetectionResult(
            model="fake.pt", image_width=600, image_height=400, inference_ms=12.0,
            detections=[Detection(label="cell phone", confidence=0.91, box=(20, 100, 120, 200)),
                        Detection(label="laptop", confidence=0.8, box=(150, 50, 350, 250))])
        self.calls: list[bytes] = []

    async def detect(self, image: bytes):
        from app.vision.detector import InvalidImageError
        self.calls.append(image)
        if image == b"not an image":
            raise InvalidImageError("not a readable image")
        return self.scene


class FakeVoice:
    """Stands in for Sarvam. `heard` is the transcript; set `fail_stt`/`fail_tts` to simulate outages."""

    def __init__(self, heard: str = "where is my phone"):
        from app.voice.sarvam import Transcript
        self.heard = Transcript(text=heard, language_code="en-IN")
        self.fail_stt = self.fail_tts = self.fail_translate = False
        self.unspeakable: set[str] = set()
        self.transcribed: list[tuple[bytes, str]] = []
        self.spoken: list[str] = []

    async def transcribe(self, audio: bytes, content_type: str):
        from app.voice.sarvam import VoiceError
        if self.fail_stt:
            raise VoiceError("Sarvam unreachable (ConnectError)")
        self.transcribed.append((audio, content_type))
        return self.heard

    async def synthesize(self, text: str, language_code: str | None = None) -> bytes:
        from app.voice.sarvam import VoiceError
        if self.fail_tts:
            raise VoiceError("Sarvam unreachable (ReadTimeout)")
        if language_code is not None and language_code in self.unspeakable:
            raise VoiceError("Sarvam HTTP 400: unsupported language", 400)
        self.spoken.append((text, language_code) if language_code else text)
        return b"RIFFfake-wav"

    async def translate(self, text: str, target_language_code: str) -> str:
        from app.voice.sarvam import VoiceError
        if self.fail_translate:
            raise VoiceError("Sarvam HTTP 429: busy")
        return f"[{target_language_code}] {text}"

    async def aclose(self) -> None:
        pass


@pytest.fixture
def vision_settings(env):
    env.update(YOLO_MODEL="data/models/test.pt", YOLO_CONFIDENCE="0.35", SCENE_MAX_AGE_S="60")
    return load_settings(env)
