"""Runtime settings read from the environment (a .env file is loaded if present).

Required values fail closed: a missing or malformed variable stops startup with
an explicit error naming the variable, instead of falling back to a guess.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Backend names the code knows how to build (see app/communication/caregiver.py).
COMMUNICATION_BACKENDS = ("MOCK", "WEBHOOK", "ANDROID_GATEWAY")
# Sarvam speech-to-text modes. "translate" turns any supported Indian language into
# English text, which is what the English intent parser understands.
SARVAM_STT_MODES = ("transcribe", "translate")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _optional(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _require(env: Mapping[str, str], name: str) -> str:
    value = _optional(env, name)
    if value is None:
        raise ConfigError(f"{name} is required but not set (see .env.example)")
    return value


def _bool(name: str, value: str) -> bool:
    lowered = value.lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise ConfigError(f"{name} must be true or false, got {value!r}")


def _number(name: str, value: str, kind: type, minimum: float) -> float | int:
    try:
        parsed = kind(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a {kind.__name__}, got {value!r}") from exc
    if parsed < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {parsed}")
    return parsed


def _phone(name: str, value: str) -> str:
    number = re.sub(r"[\s-]", "", value)
    if not re.fullmatch(r"\+\d{8,15}", number):
        raise ConfigError(f"{name} must be a full international number like +919876543210, got {value!r}")
    return number


def _contact_phones(env: Mapping[str, str]) -> dict[str, str]:
    """CAREGIVER_PHONE is the "caregiver" contact (emergency alerts). CONTACT_PHONES adds
    others as "son=+91..., daughter=+91..."; names must match the intent catalog's contacts."""
    phones: dict[str, str] = {}
    caregiver = _optional(env, "CAREGIVER_PHONE")
    if caregiver is not None:
        phones["caregiver"] = _phone("CAREGIVER_PHONE", caregiver)
    for entry in (_optional(env, "CONTACT_PHONES") or "").split(","):
        if not entry.strip():
            continue
        name, sep, number = entry.partition("=")
        if not sep or not name.strip():
            raise ConfigError(f"CONTACT_PHONES entries must look like son=+919876543210, got {entry.strip()!r}")
        phones[name.strip().lower()] = _phone(f"CONTACT_PHONES ({name.strip()})", number)
    return phones


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    mock_hardware: bool
    api_host: str
    api_port: int
    log_level: str
    assistant_language: str
    catalog_dir: Path

    esp32_controller_url: str | None
    esp32_http_timeout_s: float
    esp32_http_retries: int
    esp32_retry_backoff_s: float
    esp32_move_timeout_s: float = 20.0

    communication_backend: str
    # Contact name (as in the intent catalog) -> phone number in +<country><number> form.
    contact_phones: dict[str, str] = {}
    android_gateway_url: str | None = None
    android_gateway_username: str | None = None
    android_gateway_password: str | None = Field(default=None, repr=False)
    android_gateway_timeout_s: float | None = None
    # MacroDroid on the bedside phone places real calls. None = calls only open the dialler.
    call_automation_url: str | None = None
    call_automation_timeout_s: float | None = None

    yolo_model: Path | None = None  # None = vision disabled
    yolo_confidence: float | None = None
    scene_max_age_s: float | None = None
    # Patient condition from the camera (YOLO pose). None = off.
    yolo_pose_model: Path | None = None
    pose_interval_s: float | None = None
    still_attention_min: float | None = None
    away_attention_min: float | None = None
    sensor_poll_s: float | None = None  # None = ESP32 sensors not polled for the dashboard

    database_path: Path | None = None  # activity history for reports (needs the pose model)
    history_days: int | None = None

    llm_provider: str | None = None  # None = no AI summary (reports still have their numbers)
    openai_api_key: str | None = Field(default=None, repr=False)
    llm_model: str | None = None
    llm_timeout_s: float | None = None
    llm_assistant: bool = False  # AI understands unmatched sentences and phrases replies
    llm_assistant_timeout_s: float | None = None

    access_password: str | None = Field(default=None, repr=False)  # None = no login (local testing only)

    sarvam_api_key: str | None = Field(default=None, repr=False)  # None = voice disabled
    sarvam_stt_model: str | None = None
    sarvam_stt_mode: str | None = None
    sarvam_tts_model: str | None = None
    sarvam_tts_speaker: str | None = None
    sarvam_tts_language: str | None = None
    sarvam_timeout_s: float | None = None
    sarvam_reply_in_spoken_language: bool = False
    sarvam_translate_model: str | None = None

    https_port: int | None = None  # None = HTTP only (phone mic needs HTTPS)
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None

    @property
    def vision_labels_path(self) -> Path:
        return self.catalog_dir / "vision_labels.json"

    @property
    def intents_path(self) -> Path:
        return self.catalog_dir / "intents" / f"{self.assistant_language}.json"

    @property
    def pose_catalog_path(self) -> Path:
        return self.catalog_dir / "poses.json"

    @property
    def mock_catalog_path(self) -> Path:
        return self.catalog_dir / "mock_hardware.json"

    @property
    def responses_path(self) -> Path:
        return self.catalog_dir / "responses" / f"{self.assistant_language}.json"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from `env` (tests) or from the process environment + .env."""
    if env is None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        env = os.environ

    mock = _bool("MOCK_HARDWARE", _require(env, "MOCK_HARDWARE"))

    log_level = _require(env, "LOG_LEVEL").upper()
    if not isinstance(logging.getLevelName(log_level), int):
        raise ConfigError(f"LOG_LEVEL {log_level!r} is not a valid logging level")

    # Only real hardware needs the controller URL; mock mode ignores it entirely.
    controller_url = None
    if not mock:
        controller_url = _require(env, "ESP32_CONTROLLER_URL").rstrip("/")
        if not controller_url.startswith(("http://", "https://")) or "x.x" in controller_url:
            raise ConfigError(f"ESP32_CONTROLLER_URL is not a usable URL: {controller_url!r}")

    communication_backend = _require(env, "COMMUNICATION_BACKEND").upper()
    if communication_backend not in COMMUNICATION_BACKENDS:
        raise ConfigError(f"COMMUNICATION_BACKEND must be one of {', '.join(COMMUNICATION_BACKENDS)}, "
                          f"got {communication_backend!r}")

    contact_phones = _contact_phones(env)
    gateway: dict[str, object] = {}
    if communication_backend == "ANDROID_GATEWAY":
        gateway_url = _require(env, "ANDROID_GATEWAY_URL").rstrip("/")
        if not gateway_url.startswith(("http://", "https://")):
            raise ConfigError(f"ANDROID_GATEWAY_URL is not a usable URL: {gateway_url!r}")
        if "caregiver" not in contact_phones:
            raise ConfigError("CAREGIVER_PHONE is required with COMMUNICATION_BACKEND=ANDROID_GATEWAY "
                              "(emergency alerts go to the caregiver)")
        gateway = dict(
            android_gateway_url=gateway_url,
            android_gateway_username=_require(env, "ANDROID_GATEWAY_USERNAME"),
            android_gateway_password=_require(env, "ANDROID_GATEWAY_PASSWORD"),
            android_gateway_timeout_s=_number("ANDROID_GATEWAY_TIMEOUT_S", _require(env, "ANDROID_GATEWAY_TIMEOUT_S"),
                                              float, 0.5),
        )

    calls: dict[str, object] = {}
    call_url = _optional(env, "CALL_AUTOMATION_URL")
    if call_url is not None:
        if not call_url.startswith(("http://", "https://")):
            raise ConfigError(f"CALL_AUTOMATION_URL is not a usable URL: {call_url!r}")
        calls = dict(call_automation_url=call_url,
                     call_automation_timeout_s=_number("CALL_AUTOMATION_TIMEOUT_S",
                                                       _require(env, "CALL_AUTOMATION_TIMEOUT_S"), float, 0.5))

    # Vision is optional: without YOLO_MODEL the detect endpoint answers 503.
    yolo_model = _optional(env, "YOLO_MODEL")
    yolo_confidence = scene_max_age_s = None
    if yolo_model is not None:
        yolo_confidence = _number("YOLO_CONFIDENCE", _require(env, "YOLO_CONFIDENCE"), float, 0.01)
        if yolo_confidence > 1:
            raise ConfigError(f"YOLO_CONFIDENCE must be <= 1, got {yolo_confidence}")
        scene_max_age_s = _number("SCENE_MAX_AGE_S", _require(env, "SCENE_MAX_AGE_S"), float, 1)

    pose: dict[str, object] = {}
    pose_model = _optional(env, "YOLO_POSE_MODEL")
    if pose_model is not None:
        if yolo_model is None:
            raise ConfigError("YOLO_POSE_MODEL needs YOLO_MODEL (pose runs on the live camera frames)")
        pose = dict(
            yolo_pose_model=_path(pose_model),
            pose_interval_s=_number("POSE_INTERVAL_S", _require(env, "POSE_INTERVAL_S"), float, 0.2),
            still_attention_min=_number("STILL_ATTENTION_MIN", _require(env, "STILL_ATTENTION_MIN"), float, 1),
            away_attention_min=_number("AWAY_ATTENTION_MIN", _require(env, "AWAY_ATTENTION_MIN"), float, 1),
        )
    sensor_poll = _optional(env, "SENSOR_POLL_S")

    history: dict[str, object] = {}
    database = _optional(env, "DATABASE_PATH")
    if database is not None and pose:
        history = dict(database_path=_path(database),
                       history_days=_number("HISTORY_DAYS", _require(env, "HISTORY_DAYS"), int, 1))

    llm: dict[str, object] = {}
    provider = (_optional(env, "LLM_PROVIDER") or "").lower()
    if provider == "openai" and _optional(env, "OPENAI_API_KEY"):
        llm = dict(llm_provider=provider, openai_api_key=_require(env, "OPENAI_API_KEY"),
                   llm_model=_require(env, "LLM_MODEL"),
                   llm_timeout_s=_number("LLM_TIMEOUT_S", _require(env, "LLM_TIMEOUT_S"), float, 1),
                   llm_assistant=_bool("LLM_ASSISTANT", _require(env, "LLM_ASSISTANT")))
        if llm["llm_assistant"]:
            llm["llm_assistant_timeout_s"] = _number("LLM_ASSISTANT_TIMEOUT_S",
                                                     _require(env, "LLM_ASSISTANT_TIMEOUT_S"), float, 1)
    elif provider and provider != "openai":
        raise ConfigError(f"LLM_PROVIDER={provider} is not implemented yet; use openai or leave it empty")

    # Voice is optional: without SARVAM_API_KEY only typed text works and replies aren't spoken.
    sarvam: dict[str, object] = {}
    sarvam_api_key = _optional(env, "SARVAM_API_KEY")
    if sarvam_api_key is not None:
        sarvam_stt_mode = _require(env, "SARVAM_STT_MODE").lower()
        if sarvam_stt_mode not in SARVAM_STT_MODES:
            raise ConfigError(f"SARVAM_STT_MODE must be one of {', '.join(SARVAM_STT_MODES)}, got {sarvam_stt_mode!r}")
        sarvam = dict(
            sarvam_api_key=sarvam_api_key,
            sarvam_stt_model=_require(env, "SARVAM_STT_MODEL"),
            sarvam_stt_mode=sarvam_stt_mode,
            sarvam_tts_model=_require(env, "SARVAM_TTS_MODEL"),
            sarvam_tts_speaker=_require(env, "SARVAM_TTS_SPEAKER").lower(),
            sarvam_tts_language=_require(env, "SARVAM_TTS_LANGUAGE"),
            sarvam_timeout_s=_number("SARVAM_TIMEOUT_S", _require(env, "SARVAM_TIMEOUT_S"), float, 1),
            sarvam_reply_in_spoken_language=_bool("SARVAM_REPLY_IN_SPOKEN_LANGUAGE",
                                                  _require(env, "SARVAM_REPLY_IN_SPOKEN_LANGUAGE")),
        )
        if sarvam["sarvam_reply_in_spoken_language"]:
            sarvam["sarvam_translate_model"] = _require(env, "SARVAM_TRANSLATE_MODEL")

    # HTTPS is optional and runs next to plain HTTP. Phone browsers only allow the
    # microphone and live camera on HTTPS pages.
    tls: dict[str, object] = {}
    https_port = _optional(env, "HTTPS_PORT")
    if https_port is not None:
        tls = dict(https_port=_number("HTTPS_PORT", https_port, int, 1))
        for name in ("TLS_CERT_FILE", "TLS_KEY_FILE"):
            path = _path(_require(env, name))
            if not path.is_file():
                raise ConfigError(f"{name} {path} does not exist (see README: HTTPS for the phone)")
            tls[name.lower()] = path

    catalog_dir = _path(_require(env, "CATALOG_DIR"))
    if not catalog_dir.is_dir():
        raise ConfigError(f"CATALOG_DIR {catalog_dir} does not exist")

    return Settings(
        mock_hardware=mock,
        api_host=_require(env, "API_HOST"),
        api_port=_number("API_PORT", _require(env, "API_PORT"), int, 1),
        log_level=log_level,
        assistant_language=_require(env, "ASSISTANT_LANGUAGE").lower(),
        catalog_dir=catalog_dir,
        esp32_controller_url=controller_url,
        esp32_http_timeout_s=_number("ESP32_HTTP_TIMEOUT_S", _require(env, "ESP32_HTTP_TIMEOUT_S"), float, 0.1),
        esp32_http_retries=_number("ESP32_HTTP_RETRIES", _require(env, "ESP32_HTTP_RETRIES"), int, 0),
        esp32_retry_backoff_s=_number("ESP32_RETRY_BACKOFF_S", _require(env, "ESP32_RETRY_BACKOFF_S"), float, 0),
        **({"esp32_move_timeout_s": _number("ESP32_MOVE_TIMEOUT_S", _require(env, "ESP32_MOVE_TIMEOUT_S"), float, 1)}
           if not mock else {}),
        communication_backend=communication_backend,
        contact_phones=contact_phones,
        **gateway,
        **calls,
        yolo_model=_path(yolo_model) if yolo_model else None,
        yolo_confidence=yolo_confidence,
        scene_max_age_s=scene_max_age_s,
        **pose,
        sensor_poll_s=_number("SENSOR_POLL_S", sensor_poll, float, 1) if sensor_poll else None,
        **history,
        **llm,
        access_password=_optional(env, "ACCESS_PASSWORD"),
        **sarvam,
        **tls,
    )
