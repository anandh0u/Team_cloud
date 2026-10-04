"""Real phone calls placed by the bedside Android phone, with no tap needed.

MacroDroid on the phone runs an "HTTP Server Request" trigger (local Wi-Fi only) whose
macro does "Make Call" with the number from the request and turns the speaker on. The
Pi only sends: GET <CALL_AUTOMATION_URL>?number=+91...
"""
from __future__ import annotations

import httpx

from app.config import Settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PhoneAutomationDialer:
    name = "PHONE_AUTOMATION"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._url = settings.call_automation_url
        self._http = httpx.AsyncClient(timeout=settings.call_automation_timeout_s, transport=transport)

    async def call(self, number: str) -> None:
        # Never retried: a repeated request would ring the person twice.
        response = await self._http.get(self._url, params={"number": number})
        if response.status_code >= 300:
            raise ConnectionError(f"phone automation HTTP {response.status_code}: {response.text[:200]}")
        logger.info("bedside phone is calling ...%s", number[-3:])

    async def aclose(self) -> None:
        await self._http.aclose()
