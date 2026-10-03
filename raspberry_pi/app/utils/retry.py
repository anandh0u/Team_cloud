"""Async retry with linear backoff, for calls that are safe to repeat."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.utils.logger import get_logger

T = TypeVar("T")
logger = get_logger(__name__)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    attempts: int,
    backoff_s: float,
    retry_on: tuple[type[BaseException], ...],
    label: str,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except retry_on as exc:
            if attempt == attempts:
                raise
            delay = backoff_s * attempt
            logger.warning("%s failed (%s), retry %d/%d in %.2fs",
                           label, type(exc).__name__, attempt, attempts - 1, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
