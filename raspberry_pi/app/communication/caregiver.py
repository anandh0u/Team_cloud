"""Caregiver communication: send_message, call_contact, send_emergency_alert.

The backend comes from COMMUNICATION_BACKEND. Every call is wrapped so a backend
failure returns CommResult(ok=False) instead of raising, and the assistant can
tell the user truthfully that the message did not go through.
"""
from __future__ import annotations

from typing import Protocol

from app.config import ConfigError, Settings
from app.models import CommResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CaregiverChannel(Protocol):
    name: str

    async def send_message(self, contact: str, message: str) -> None: ...
    async def call(self, contact: str) -> None: ...


class CaregiverService:
    def __init__(self, channel: CaregiverChannel, emergency_contact: str):
        self._channel = channel
        self.emergency_contact = emergency_contact

    @property
    def backend(self) -> str:
        return self._channel.name

    async def _run(self, kind: str, contact: str, coro) -> CommResult:
        try:
            await coro
        except Exception as exc:  # backend errors must never crash the assistant
            logger.error("caregiver %s to %s via %s FAILED: %s", kind, contact, self.backend, type(exc).__name__)
            return CommResult(ok=False, kind=kind, contact=contact, backend=self.backend,
                              error=f"{type(exc).__name__}: {exc}")
        logger.warning("caregiver %s to %s sent via %s", kind, contact, self.backend)
        return CommResult(ok=True, kind=kind, contact=contact, backend=self.backend)

    async def send_message(self, contact: str, message: str) -> CommResult:
        return await self._run("message", contact, self._channel.send_message(contact, message))

    async def call_contact(self, contact: str) -> CommResult:
        return await self._run("call", contact, self._channel.call(contact))

    async def send_emergency_alert(self, message: str) -> CommResult:
        return await self._run("emergency_alert", self.emergency_contact,
                               self._channel.send_message(self.emergency_contact, message))


def build_caregiver(settings: Settings, emergency_contact: str) -> CaregiverService:
    """`emergency_contact` is the first entry of the intent catalog's emergency_call_contacts."""
    if settings.communication_backend == "MOCK":
        from app.communication.mock import MockChannel

        logger.warning("COMMUNICATION_BACKEND=MOCK: caregiver messages, calls and alerts are only LOGGED")
        return CaregiverService(MockChannel(), emergency_contact)
    raise ConfigError(f"COMMUNICATION_BACKEND={settings.communication_backend} is not implemented yet; use MOCK")
