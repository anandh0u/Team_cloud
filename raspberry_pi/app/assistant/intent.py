"""Deterministic intent parser driven by config/intents/<lang>.json.

Order of checks (first match wins):
  EMERGENCY > STOP > HOME > STATUS > MESSAGE_CONTACT > CALL_CONTACT > FIND_OBJECT > GET_OBJECT > UNKNOWN

EMERGENCY and STOP are decided here, locally, before anything else; an LLM
(phase 10) will only ever see utterances that end up UNKNOWN.
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.assistant.emergency import contains_phrase, detect_emergency, detect_stop
from app.catalog import load_json_catalog
from app.config import ConfigError
from app.models import Action, Intent
from app.utils.logger import get_logger

logger = get_logger(__name__)

_PLACEHOLDERS = {
    "{object}": r"(?P<object>.+?)",
    "{contact}": r"(?P<contact>.+?)",
    "{message}": r"(?P<message>.+)",
}


class IntentCatalog(BaseModel):
    contractions: dict[str, str]
    filler_start: list[str]
    filler_end: list[str]
    emergency_exact: list[str]
    emergency_contains: list[str]
    emergency_call_contacts: list[str]
    stop_words: list[str]
    home_phrases: list[str]
    release_phrases: list[str]
    status_phrases: list[str]
    message_patterns: list[str]
    call_patterns: list[str]
    find_patterns: list[str]
    get_patterns: list[str]
    objects: dict[str, list[str]]
    contacts: dict[str, list[str]]
    default_message: str

    @classmethod
    def load(cls, path: Path) -> IntentCatalog:
        data = {k: v for k, v in load_json_catalog(path).items() if not k.startswith("_")}
        try:
            catalog = cls.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(f"Intent catalog {path} is invalid: {exc}") from exc
        if not catalog.emergency_call_contacts:
            raise ConfigError(f"Intent catalog {path}: emergency_call_contacts must name at least one contact")
        unknown = set(catalog.emergency_call_contacts) - set(catalog.contacts)
        if unknown:
            raise ConfigError(f"Intent catalog {path}: emergency_call_contacts not in contacts: {sorted(unknown)}")
        return catalog

    @property
    def emergency_contact(self) -> str:
        return self.emergency_call_contacts[0]


def _compile(template: str) -> re.Pattern[str]:
    regex = re.escape(template)
    for placeholder, group in _PLACEHOLDERS.items():
        regex = regex.replace(re.escape(placeholder), group)
    return re.compile(f"^{regex}$")


class IntentParser:
    def __init__(self, catalog: IntentCatalog):
        self.catalog = catalog
        self._message = [(t, _compile(t)) for t in catalog.message_patterns]
        self._call = [(t, _compile(t)) for t in catalog.call_patterns]
        self._find = [(t, _compile(t)) for t in catalog.find_patterns]
        self._get = [(t, _compile(t)) for t in catalog.get_patterns]
        # Longest fillers first so "can you" is trimmed before "can".
        self._start = sorted(catalog.filler_start, key=len, reverse=True)
        self._end = sorted(catalog.filler_end, key=len, reverse=True)

    def normalize(self, text: str) -> str:
        t = text.lower().replace("’", "'")
        t = re.sub(r"[^a-z0-9' ]+", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        words = [self.catalog.contractions.get(w, w) for w in t.split(" ")]
        t = " ".join(words)
        changed = True
        while changed and t:
            changed = False
            for f in self._start:
                if t == f or t.startswith(f + " "):
                    t, changed = t[len(f):].strip(), True
            for f in self._end:
                if t == f or t.endswith(" " + f):
                    t, changed = t[: len(t) - len(f)].strip(), True
        return t

    @staticmethod
    def _resolve(captured: str, table: dict[str, list[str]]) -> str | None:
        """Map free text to a canonical name using whole-word alias matches; longest alias wins."""
        best, best_len = None, 0
        for canonical, aliases in table.items():
            for alias in aliases:
                if len(alias) > best_len and contains_phrase(captured, alias):
                    best, best_len = canonical, len(alias)
        return best

    def resolve_object(self, text: str) -> str | None:
        return self._resolve(self.normalize(text), self.catalog.objects)

    def _first(self, patterns, text):
        for template, regex in patterns:
            m = regex.match(text)
            if m:
                return template, m.groupdict()
        return None, None

    def _message_parts(self, text: str) -> tuple[str | None, str | None, str | None]:
        template, groups = self._first(self._message, text)
        if template is None:
            return None, None, None
        contact = self._resolve(groups["contact"], self.catalog.contacts)
        message = groups.get("message") or self.catalog.default_message
        return template, contact, message[:1].upper() + message[1:]

    def parse(self, text: str) -> Intent:
        norm = self.normalize(text)
        c = self.catalog

        def intent(action: Action, matched: str | None = None, **kw) -> Intent:
            result = Intent(action=action, matched=matched, text=text, **kw)
            logger.info("intent %s object=%s contact=%s matched=%r text=%r",
                        action.value, result.object, result.contact, matched, text)
            return result

        if not norm:
            return intent(Action.UNKNOWN)

        phrase = detect_emergency(norm, c.emergency_exact, c.emergency_contains)
        if phrase:
            # Still capture "tell my daughter I need help" so the router can message her too.
            _, contact, message = self._message_parts(norm)
            return intent(Action.EMERGENCY, phrase, contact=contact, message=message)

        word = detect_stop(norm, c.stop_words)
        if word:
            return intent(Action.STOP, word)

        for p in c.home_phrases:
            if contains_phrase(norm, p):
                return intent(Action.HOME, p)
        for p in c.release_phrases:
            if contains_phrase(norm, p):
                return intent(Action.RELEASE, p)
        for p in c.status_phrases:
            if contains_phrase(norm, p):
                return intent(Action.STATUS, p)

        template, contact, message = self._message_parts(norm)
        if template:
            return intent(Action.MESSAGE_CONTACT, template, contact=contact, message=message)

        template, groups = self._first(self._call, norm)
        if template:
            contact = self._resolve(groups["contact"], c.contacts)
            if contact in c.emergency_call_contacts:
                return intent(Action.EMERGENCY, template, contact=contact)
            return intent(Action.CALL_CONTACT, template, contact=contact)

        for action, patterns in ((Action.FIND_OBJECT, self._find), (Action.GET_OBJECT, self._get)):
            template, groups = self._first(patterns, norm)
            if template:
                return intent(action, template, object=self._resolve(groups["object"], c.objects))

        return intent(Action.UNKNOWN)
