"""Real SMS through an Android phone running the "SMS Gateway for Android" app
(https://sms-gate.app) in Local Server mode. The phone's own SIM sends the texts.

The app can't place phone calls, so call() texts the contact a call-back request;
the bedside phone page opens the dialler for the actual call.
"""
from __future__ import annotations

import httpx

from app.config import Settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AndroidGatewayChannel:
    name = "ANDROID_GATEWAY"

    def __init__(self, settings: Settings, sms_texts: dict[str, str],
                 transport: httpx.AsyncBaseTransport | None = None):
        self._phones = settings.contact_phones
        self._texts = sms_texts  # "message" (with {message}) and "call_request"
        self._http = httpx.AsyncClient(
            base_url=settings.android_gateway_url, timeout=settings.android_gateway_timeout_s, transport=transport,
            auth=(settings.android_gateway_username, settings.android_gateway_password))

    def _number(self, contact: str) -> str:
        number = self._phones.get(contact)
        if number is None:
            raise LookupError(f"no phone number for '{contact}' (see CAREGIVER_PHONE / CONTACT_PHONES in .env)")
        return number

    async def _sms(self, contact: str, text: str) -> None:
        number = self._number(contact)
        response = await self._http.post("/message", json={"textMessage": {"text": text}, "phoneNumbers": [number]})
        if response.status_code >= 300:
            raise ConnectionError(f"gateway HTTP {response.status_code}: {response.text[:200]}")
        # The number is personal data: log only its last digits.
        logger.info("gateway accepted SMS to %s (...%s): HTTP %d", contact, number[-3:], response.status_code)

    async def send_message(self, contact: str, message: str) -> None:
        await self._sms(contact, self._texts["message"].format(message=message))

    async def call(self, contact: str) -> None:
        await self._sms(contact, self._texts["call_request"])

    async def aclose(self) -> None:
        await self._http.aclose()
