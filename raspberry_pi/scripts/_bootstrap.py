"""Shared setup for scripts/: puts the project on sys.path and loads config + catalogs."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.assistant.responses import Responses  # noqa: E402
from app.config import ConfigError, Settings, load_settings  # noqa: E402
from app.hardware.poses import PoseCatalog  # noqa: E402
from app.utils.logger import setup_logging  # noqa: E402


def bootstrap() -> tuple[Settings, PoseCatalog, Responses]:
    try:
        settings = load_settings()
        setup_logging(settings.log_level)
        poses = PoseCatalog.load(settings.pose_catalog_path)
        responses = Responses.load(settings.responses_path, settings.assistant_language)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)
    mode = "MOCK (simulated hardware)" if settings.mock_hardware else f"REAL controller at {settings.esp32_controller_url}"
    print(f"== mode: {mode}")
    return settings, poses, responses
