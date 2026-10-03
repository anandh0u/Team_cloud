"""Emergency detection and handling. Never involves an LLM.

Detection works on already-normalised text (see intent.normalize). Handling stops
the arm and alerts the caregiver *at the same time*, so an unreachable ESP32 can
never delay the alert, and the spoken reply never claims an alert that failed.
"""
from __future__ import annotations

import asyncio
import re

from app.assistant.responses import Responses
from app.communication.caregiver import CaregiverService
from app.hardware.arm import ArmService
from app.models import EmergencyResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

RESPONSE_KEYS = ("emergency_contacting", "emergency_alert_failed", "emergency_alert_message")


def contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9']){re.escape(phrase)}(?![a-z0-9'])", text) is not None


def detect_emergency(normalized: str, exact: list[str], contains: list[str]) -> str | None:
    """Return the phrase that marks `normalized` as an emergency, or None."""
    if normalized in exact:
        return normalized
    for phrase in contains:
        if contains_phrase(normalized, phrase):
            return phrase
    return None


def detect_stop(normalized: str, stop_words: list[str]) -> str | None:
    for word in stop_words:
        if contains_phrase(normalized, word):
            return word
    return None


class EmergencyHandler:
    def __init__(self, arm: ArmService, caregiver: CaregiverService, responses: Responses):
        responses.require(RESPONSE_KEYS)
        self._arm = arm
        self._caregiver = caregiver
        self._responses = responses

    async def trigger(self, source: str, text: str | None = None) -> EmergencyResult:
        logger.critical("EMERGENCY triggered source=%s text=%r", source, text)
        arm_result, alert = await asyncio.gather(
            self._arm.stop(),
            self._caregiver.send_emergency_alert(self._responses.get("emergency_alert_message")),
        )
        key = "emergency_contacting" if alert.ok else "emergency_alert_failed"
        logger.critical("EMERGENCY handled: arm_stopped=%s caregiver_alerted=%s via=%s",
                        arm_result.ok, alert.ok, alert.backend)
        return EmergencyResult(arm_stopped=arm_result.ok, caregiver_alerted=alert.ok,
                               response=self._responses.get(key), arm=arm_result, alert=alert)
