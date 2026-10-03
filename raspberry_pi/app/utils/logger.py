"""Logging setup with a filter that masks secret values if they ever reach a log line."""
from __future__ import annotations

import logging
import os
import re

_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$", re.IGNORECASE)


class SecretRedactionFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            message = record.getMessage()
            redacted = message
            for secret in self._secrets:
                redacted = redacted.replace(secret, "***")
            if redacted != message:
                record.msg, record.args = redacted, None
        return True


def setup_logging(level: str) -> None:
    secrets = [v for k, v in os.environ.items() if _SECRET_NAME.search(k) and v]
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(SecretRedactionFilter(secrets))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs every request at INFO, including full URLs; our client logs its own summary.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
