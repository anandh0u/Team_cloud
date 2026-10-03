"""Loads the JSON catalogs in CATALOG_DIR (poses, spoken responses, mock values)."""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.config import ConfigError


def load_json_catalog(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Catalog file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Catalog {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Catalog {path} must contain a JSON object")
    return data


def require_keys(data: dict[str, Any], keys: Iterable[str], where: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise ConfigError(f"{where} is missing keys: {', '.join(missing)}")
