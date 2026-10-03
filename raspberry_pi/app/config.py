"""Runtime settings read from the environment (a .env file is loaded if present).

Required values fail closed: a missing or malformed variable stops startup with
an explicit error naming the variable, instead of falling back to a guess.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    )
