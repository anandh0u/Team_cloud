"""Read-only check of the ESP32 controller: health, status, telemetry, heartbeat, IMU.

Does NOT move the arm.  Usage: python scripts/test_esp32.py
Exit code 0 = every endpoint answered, 1 = at least one failed.
"""
import asyncio
import sys

from _bootstrap import bootstrap

from app.hardware.esp32_client import build_controller
from app.hardware.telemetry import TelemetryService


async def main() -> int:
    settings, poses, _ = bootstrap()
    client = build_controller(settings, poses)
    failures = 0
    try:
        for name in ("health", "status", "telemetry", "heartbeat", "imu"):
            result = await getattr(client, name)()
            mark = "OK  " if result.ok else "FAIL"
            failures += not result.ok
            print(f"[{mark}] {result.endpoint:<16} source={result.source.value} "
                  f"status={result.status_code} latency_ms={result.latency_ms} "
                  f"error={result.error} data={result.data}")

        snap = await TelemetryService(client).snapshot()
        print(f"\nparsed telemetry: available={snap.available} source={snap.source.value}\n"
              f"  heartbeat={snap.heartbeat}\n  imu={snap.imu}\n  arm={snap.arm} gripper={snap.gripper} "
              f"emergency_stop={snap.emergency_stop}\n  errors={snap.errors}")
        failures += not snap.available or bool(snap.errors)
    finally:
        await client.aclose()

    print("\nRESULT:", "all endpoints OK" if failures == 0 else f"{failures} check(s) failed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
