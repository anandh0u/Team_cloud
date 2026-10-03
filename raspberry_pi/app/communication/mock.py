"""MOCK caregiver channel: logs what would be sent and keeps a record for the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.utils.logger import get_logger

logger = get_logger(__name__)


class MockChannel:
    name = "MOCK"

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail = False  # tests flip this to simulate a backend outage

    def _record(self, kind: str, contact: str, message: str | None) -> None:
        if self.fail:
            raise ConnectionError("simulated communication failure")
        entry = {"kind": kind, "contact": contact, "message": message,
                 "timestamp": datetime.now(timezone.utc).isoformat()}
        self.sent.append(entry)
        logger.warning("[MOCK %s] to=%s message=%r (nothing was actually sent)", kind, contact, message)

    async def send_message(self, contact: str, message: str) -> None:
        self._record("message", contact, message)

    async def call(self, contact: str) -> None:
        self._record("call", contact, None)
