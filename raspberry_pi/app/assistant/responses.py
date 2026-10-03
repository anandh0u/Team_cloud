"""Spoken / returned phrases, loaded per language from config/responses/<lang>.json."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from app.catalog import load_json_catalog, require_keys
from app.config import ConfigError


class Responses:
    def __init__(self, phrases: dict[str, str], language: str):
        self._phrases = phrases
        self.language = language

    @classmethod
    def load(cls, path: Path, language: str) -> Responses:
        data = load_json_catalog(path)
        phrases = {k: v for k, v in data.items() if not k.startswith("_")}
        bad = [k for k, v in phrases.items() if not isinstance(v, str) or not v.strip()]
        if bad:
            raise ConfigError(f"Responses catalog {path} has empty or non-text entries: {', '.join(bad)}")
        return cls(phrases, language)

    def require(self, keys: Iterable[str]) -> None:
        """Fail at startup if a module needs a phrase this language doesn't define."""
        require_keys(self._phrases, keys, f"Responses catalog '{self.language}'")

    def get(self, key: str, **values: str) -> str:
        return self._phrases[key].format(**values)
