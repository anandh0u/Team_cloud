"""Client for the ESP32 controller's HTTP API (arm, gripper, heartbeat, MPU6050).

Every call has a timeout, catches its own exceptions, logs a one-line summary
and returns a ControllerResult. Nothing here raises on network failure.

Only safe-to-repeat calls (GETs, /stop, /resume) are retried. Motion commands
are sent at most once: a timed-out move is reported as failed, never re-sent.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.hardware.poses import PoseCatalog
from app.models import ControllerResult, DataSource
from app.utils.logger import get_logger
from app.utils.retry import retry_async

logger = get_logger(__name__)


class ControllerClient(Protocol):
    source: DataSource

    async def health(self) -> ControllerResult: ...
    async def status(self) -> ControllerResult: ...
    async def telemetry(self) -> ControllerResult: ...
    async def heartbeat(self) -> ControllerResult: ...
    async def imu(self) -> ControllerResult: ...
    async def arm_home(self) -> ControllerResult: ...
    async def arm_pose(self, pose: str) -> ControllerResult: ...
    async def gripper_open(self) -> ControllerResult: ...
    async def gripper_close(self) -> ControllerResult: ...
    async def stop(self) -> ControllerResult: ...
    async def resume(self) -> ControllerResult: ...
    async def aclose(self) -> None: ...


class HttpEsp32Client:
    source = DataSource.DEVICE

    def __init__(self, base_url: str, timeout_s: float, retries: int, backoff_s: float,
                 move_timeout_s: float = 20.0, poll_s: float = 0.2,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s, transport=transport)
        self._retries = retries
        self._backoff_s = backoff_s
        self._move_timeout_s = move_timeout_s
        self._poll_s = poll_s

    async def _request(self, method: str, path: str, *, repeatable: bool,
                       json: dict[str, Any] | None = None, quiet: bool = False) -> ControllerResult:
        endpoint = f"{method} {path}"
        attempts = 1 + self._retries if repeatable else 1
        started = time.perf_counter()

        def elapsed() -> float:
            return round((time.perf_counter() - started) * 1000, 1)

        try:
            response = await retry_async(
                lambda: self._client.request(method, path, json=json),
                attempts=attempts, backoff_s=self._backoff_s,
                retry_on=(httpx.TransportError,), label=f"esp32 {endpoint}")
        except httpx.TimeoutException:
            logger.error("esp32 %s -> timeout after %.0f ms", endpoint, elapsed())
            return ControllerResult(ok=False, endpoint=endpoint, source=self.source,
                                    error="timeout", latency_ms=elapsed())
        except httpx.HTTPError as exc:
            logger.error("esp32 %s -> unreachable (%s)", endpoint, type(exc).__name__)
            return ControllerResult(ok=False, endpoint=endpoint, source=self.source,
                                    error=f"unreachable: {type(exc).__name__}", latency_ms=elapsed())

        data = _json_body(response)
        ok = response.is_success
        error = None if ok else f"HTTP {response.status_code}"
        # Command replies carry their own verdict ({"ok": false, ...}); HTTP 200 alone isn't success.
        if ok and data is not None and data.get("ok") is False:
            ok = False
            error = f"controller refused: {data.get('error', 'ok=false')}"
        log = (logger.debug if quiet else logger.info) if ok else logger.warning
        log("esp32 %s -> %d in %.0f ms", endpoint, response.status_code, elapsed())
        return ControllerResult(ok=ok, endpoint=endpoint, source=self.source,
                                status_code=response.status_code, data=data,
                                error=error, latency_ms=elapsed())

    async def health(self) -> ControllerResult:
        return await self._request("GET", "/health", repeatable=True)

    async def status(self) -> ControllerResult:
        return await self._request("GET", "/status", repeatable=True)

    async def telemetry(self) -> ControllerResult:
        return await self._request("GET", "/telemetry", repeatable=True)

    async def heartbeat(self) -> ControllerResult:
        return await self._request("GET", "/heartbeat", repeatable=True)

    async def imu(self) -> ControllerResult:
        return await self._request("GET", "/imu", repeatable=True)

    async def _motion(self, path: str, json: dict[str, Any] | None = None) -> ControllerResult:
        """Motion commands return at once with "moving": true, so the controller stays free
        to accept /stop while the arm moves. Here we wait for the move to finish by polling
        /status; the step only counts as done once the arm is still."""
        result = await self._request("POST", path, repeatable=False, json=json)
        if not result.ok or not (result.data or {}).get("moving"):
            return result
        deadline = time.perf_counter() + self._move_timeout_s
        while time.perf_counter() < deadline:
            await asyncio.sleep(self._poll_s)
            status = await self._request("GET", "/status", repeatable=True, quiet=True)
            if not status.ok or status.data is None:
                continue  # a missed poll isn't a failed move; keep watching until the deadline
            if status.data.get("emergency_stop"):
                logger.warning("esp32 %s interrupted by emergency stop", result.endpoint)
                return result.model_copy(update={"ok": False, "error": "stopped during motion (emergency stop)"})
            if not (status.data.get("arm") or {}).get("moving"):
                return result.model_copy(update={"data": {**result.data, "moving": False}})
        logger.error("esp32 %s did not finish within %.0f s", result.endpoint, self._move_timeout_s)
        return result.model_copy(update={"ok": False, "error": f"motion did not finish within {self._move_timeout_s:.0f} s"})

    async def arm_home(self) -> ControllerResult:
        return await self._motion("/arm/home")

    async def arm_pose(self, pose: str) -> ControllerResult:
        return await self._motion("/arm/pose", json={"pose": pose})

    async def gripper_open(self) -> ControllerResult:
        return await self._motion("/gripper/open")

    async def gripper_close(self) -> ControllerResult:
        return await self._motion("/gripper/close")

    async def stop(self) -> ControllerResult:
        return await self._request("POST", "/stop", repeatable=True)

    async def resume(self) -> ControllerResult:
        return await self._request("POST", "/resume", repeatable=True)

    async def aclose(self) -> None:
        await self._client.aclose()


def _json_body(response: httpx.Response) -> dict[str, Any] | None:
    if not response.content:
        return None
    try:
        body = response.json()
    except ValueError:
        logger.warning("esp32 returned non-JSON body (%d bytes)", len(response.content))
        return None
    return body if isinstance(body, dict) else {"value": body}


def build_controller(settings: Settings, poses: PoseCatalog) -> ControllerClient:
    if settings.mock_hardware:
        from app.catalog import load_json_catalog
        from app.hardware.mock_controller import MockEsp32Client

        logger.warning("MOCK_HARDWARE=true: ESP32 controller, heartbeat and MPU6050 are SIMULATED")
        return MockEsp32Client(load_json_catalog(settings.mock_catalog_path), poses)

    assert settings.esp32_controller_url is not None  # enforced by load_settings
    return HttpEsp32Client(
        base_url=settings.esp32_controller_url,
        timeout_s=settings.esp32_http_timeout_s,
        retries=settings.esp32_http_retries,
        backoff_s=settings.esp32_retry_backoff_s,
        move_timeout_s=settings.esp32_move_timeout_s,
    )
