"""Moves the arm through: resume -> home -> <pose> -> gripper close -> gripper open -> home,
then checks that STOP blocks motion.

Usage:
  python scripts/test_arm.py --pose USER            (mock mode)
  python scripts/test_arm.py --pose USER --yes      (REAL arm: required confirmation)

With real hardware, keep hands and the patient clear of the arm.
"""
import argparse
import asyncio
import sys

from _bootstrap import bootstrap

from app.hardware.arm import ArmService
from app.hardware.esp32_client import build_controller


def show(step: str, result) -> bool:
    mark = "OK  " if result.ok else "FAIL"
    source = result.controller.source.value if result.controller else "-"
    print(f"[{mark}] {step:<22} pose={result.pose} source={source} "
          f"error={result.controller.error if result.controller else None} message={result.message}")
    return result.ok


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pose", required=True, help="named pose from config/poses.json")
    parser.add_argument("--yes", action="store_true", help="confirm moving REAL hardware")
    args = parser.parse_args()

    settings, poses, responses = bootstrap()
    if not settings.mock_hardware and not args.yes:
        print("Refusing to move the REAL arm without --yes.", file=sys.stderr)
        return 2
    if not poses.is_allowed(args.pose):
        print(f"Pose {args.pose!r} is not in the catalog. Allowed: {', '.join(poses.poses)}", file=sys.stderr)
        return 2

    client = build_controller(settings, poses)
    arm = ArmService(client, poses, responses)
    ok = True
    try:
        ok &= show("resume", await arm.resume())
        ok &= show("home", await arm.home())
        ok &= show(f"pose {args.pose}", await arm.move_to_pose(args.pose))
        ok &= show("gripper close", await arm.gripper_close())
        ok &= show("gripper open", await arm.gripper_open())
        ok &= show("home", await arm.home())

        print("\n-- safety check: STOP must block motion --")
        ok &= show("stop", await arm.stop())
        blocked = await arm.move_to_pose(poses.home_pose)
        if blocked.ok:
            print("[FAIL] move while stopped     SAFETY: controller accepted a move while stopped")
            ok = False
        else:
            print(f"[OK  ] move while stopped     refused as expected "
                  f"(error={blocked.controller.error if blocked.controller else None})")
        ok &= show("resume", await arm.resume())

        print("\n-- local check: unknown pose never reaches the controller --")
        local = await arm.move_to_pose("NOT_A_POSE")
        print(f"[{'OK  ' if not local.ok and local.controller is None else 'FAIL'}] unknown pose rejected locally: {local.message}")
        ok &= not local.ok and local.controller is None
    finally:
        await client.aclose()

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
