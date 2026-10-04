"""Caregiver communication: send_message, call_contact, send_emergency_alert.

The backend comes from COMMUNICATION_BACKEND. Every call is wrapped so a backend
failure returns CommResult(ok=False) instead of raising, and the assistant can
tell the user truthfully that the message did not go through.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Protocol

from app.config import ConfigError, Settings
from app.models import CommResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CaregiverChannel(Protocol):
    name: str

    async def send_message(self, contact: str, message: str) -> None: ...
    async def call(self, contact: str) -> None: ...


class PhoneDialer(Protocol):
    name: str

    async def call(self, number: str) -> None: ...


class CaregiverService:
    def __init__(self, channel: CaregiverChannel, emergency_contact: str, dialer: PhoneDialer | None = None,
                 phones: dict[str, str] | None = None):
        """`dialer` places real calls (bedside phone automation); without it a call goes
        through `channel` (a call-back SMS, or a log line in MOCK)."""
        self._channel = channel
        self._dialer = dialer
        self._phones = phones or {}
        self.emergency_contact = emergency_contact
        self.on_result: Callable[[CommResult], None] | None = None  # e.g. the dashboard event log

    @property
    def backend(self) -> str:
        return self._channel.name

    async def _run(self, kind: str, contact: str, coro, backend: str | None = None) -> CommResult:
        backend = backend or self.backend
        try:
            await coro
        except Exception as exc:  # backend errors must never crash the assistant
            logger.error("caregiver %s to %s via %s FAILED: %s", kind, contact, backend, type(exc).__name__)
            result = CommResult(ok=False, kind=kind, contact=contact, backend=backend,
                                error=f"{type(exc).__name__}: {exc}")
        else:
            logger.warning("caregiver %s to %s sent via %s", kind, contact, backend)
            result = CommResult(ok=True, kind=kind, contact=contact, backend=backend,
                                call_placed=kind == "call" and self._dialer is not None and backend == self._dialer.name)
        if self.on_result is not None:
            self.on_result(result)
        return result

    async def _dial(self, contact: str):
        number = self._phones.get(contact)
        if number is None:
            raise LookupError(f"no phone number for '{contact}' (see CAREGIVER_PHONE / CONTACT_PHONES in .env)")
        await self._dialer.call(number)

    async def send_message(self, contact: str, message: str) -> CommResult:
        return await self._run("message", contact, self._channel.send_message(contact, message))

    async def call_contact(self, contact: str) -> CommResult:
        if self._dialer is not None:
            return await self._run("call", contact, self._dial(contact), backend=self._dialer.name)
        return await self._run("call", contact, self._channel.call(contact))

    async def send_emergency_alert(self, message: str) -> CommResult:
        """Texts the caregiver and, with a dialler, also calls them at the same time.
        The alert counts as delivered if either got through."""
        contact = self.emergency_contact
        alert = self._run("emergency_alert", contact, self._channel.send_message(contact, message))
        if self._dialer is None:
            return await alert
        sms, call = await asyncio.gather(alert, self._run("call", contact, self._dial(contact),
                                                          backend=self._dialer.name))
        errors = [r.error for r in (sms, call) if r.error]
        return CommResult(ok=sms.ok or call.ok, kind="emergency_alert", contact=contact,
                          backend=f"{sms.backend}+{call.backend}", error="; ".join(errors) or None,
                          call_placed=call.ok)

    async def aclose(self) -> None:
        for part in (self._channel, self._dialer):
            close = getattr(part, "aclose", None)  # only network channels hold connections
            if close is not None:
                await close()


def build_caregiver(settings: Settings, emergency_contact: str, sms_texts: dict[str, str],
                    known_contacts: Iterable[str]) -> CaregiverService:
    """`emergency_contact` is the first entry of the intent catalog's emergency_call_contacts."""
    unknown = sorted(set(settings.contact_phones) - set(known_contacts))
    if unknown:
        raise ConfigError(f"Phone numbers set for contacts the intent catalog doesn't know: {', '.join(unknown)}")
    dialer = None
    if settings.call_automation_url:
        from app.communication.phone_dialer import PhoneAutomationDialer

        logger.info("calls are placed automatically by the bedside phone")
        dialer = PhoneAutomationDialer(settings)
    if settings.communication_backend == "ANDROID_GATEWAY":
        from app.communication.android_gateway import AndroidGatewayChannel

        logger.info("caregiver messages go out as real SMS via %s", settings.android_gateway_url)
        channel = AndroidGatewayChannel(settings, sms_texts)
    elif settings.communication_backend == "MOCK":
        from app.communication.mock import MockChannel

        logger.warning("COMMUNICATION_BACKEND=MOCK: caregiver messages%s are only LOGGED",
                       "" if dialer else ", calls and alerts")
        channel = MockChannel()
    else:
        raise ConfigError(f"COMMUNICATION_BACKEND={settings.communication_backend} is not supported; "
                          "use MOCK or ANDROID_GATEWAY")
    return CaregiverService(channel, emergency_contact, dialer, settings.contact_phones)
