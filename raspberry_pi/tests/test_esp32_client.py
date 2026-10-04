import asyncio

import httpx

from tests.conftest import TEST_CONTROLLER_URL

from app.hardware.esp32_client import HttpEsp32Client
from app.models import DataSource


def make_client(handler, retries=2):
    calls = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        return handler(request)

    client = HttpEsp32Client(TEST_CONTROLLER_URL, timeout_s=0.5, retries=retries, backoff_s=0,
                             transport=httpx.MockTransport(recording))
    return client, calls


def run(coro):
    return asyncio.run(coro)


def test_success_returns_json_data():
    client, _ = make_client(lambda r: httpx.Response(200, json={"bpm": 70, "raw": 2000}))
    res = run(client.heartbeat())
    assert res.ok and res.status_code == 200
    assert res.data == {"bpm": 70, "raw": 2000}
    assert res.source is DataSource.DEVICE
    assert res.endpoint == "GET /heartbeat"


def test_pose_sends_named_pose_only():
    client, calls = make_client(lambda r: httpx.Response(200, json={"ok": True}))
    run(client.arm_pose("MEDICINE"))
    assert calls == [("POST", "/arm/pose", b'{"pose":"MEDICINE"}')]


def test_http_error_is_reported_not_raised():
    client, _ = make_client(lambda r: httpx.Response(500, text="boom"))
    res = run(client.status())
    assert not res.ok and res.status_code == 500 and res.error == "HTTP 500"
    assert res.data is None


def test_unreachable_get_is_retried_then_reported():
    def fail(request):
        raise httpx.ConnectError("refused", request=request)

    client, calls = make_client(fail, retries=2)
    res = run(client.health())
    assert not res.ok and res.status_code is None
    assert res.error.startswith("unreachable")
    assert len(calls) == 3


def test_timeout_is_reported():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    client, _ = make_client(slow, retries=0)
    res = run(client.telemetry())
    assert not res.ok and res.error == "timeout"


def test_motion_commands_are_never_retried():
    def fail(request):
        raise httpx.ConnectError("refused", request=request)

    for method in ("arm_home", "gripper_open", "gripper_close"):
        client, calls = make_client(fail, retries=3)
        run(getattr(client, method)())
        assert len(calls) == 1, method
    client, calls = make_client(fail, retries=3)
    run(client.arm_pose("USER"))
    assert len(calls) == 1


def test_stop_is_retried():
    def fail(request):
        raise httpx.ConnectError("refused", request=request)

    client, calls = make_client(fail, retries=2)
    run(client.stop())
    assert len(calls) == 3


def test_body_ok_false_is_a_refusal_even_with_http_200():
    client, _ = make_client(lambda r: httpx.Response(200, json={"ok": False, "error": "emergency stop active"}))
    res = run(client.arm_pose("MEDICINE"))
    assert not res.ok and res.status_code == 200
    assert res.error == "controller refused: emergency stop active"


def test_contract_replies_are_success():
    replies = {"/arm/pose": {"ok": True, "pose": "MEDICINE"},
               "/gripper/close": {"ok": True, "gripper": "CLOSED"},
               "/stop": {"ok": True, "emergency_stop": True}}
    client, _ = make_client(lambda r: httpx.Response(200, json=replies[r.url.path]))
    assert run(client.arm_pose("MEDICINE")).ok
    assert run(client.gripper_close()).ok
    assert run(client.stop()).data["emergency_stop"] is True


def test_non_json_body_gives_no_data():
    client, _ = make_client(lambda r: httpx.Response(200, text="OK"))
    res = run(client.arm_home())
    assert res.ok and res.data is None


# ---------------------------------------------------------------- waiting for motion

def _moving_controller(statuses):
    """POST /arm/pose answers "moving"; each GET /status returns the next of `statuses`."""
    import httpx
    from app.hardware.esp32_client import HttpEsp32Client
    replies = iter(statuses)
    polls = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"ok": True, "pose": "MEDICINE", "moving": True})
        polls.append(1)
        return next(replies)

    client = HttpEsp32Client("http://esp32.test", timeout_s=1, retries=0, backoff_s=0, move_timeout_s=0.3,
                             poll_s=0.01, transport=httpx.MockTransport(handler))
    return client, polls


def _status(moving, stop=False):
    import httpx
    return httpx.Response(200, json={"arm": {"pose": "MEDICINE", "moving": moving}, "emergency_stop": stop})


def test_motion_waits_until_the_arm_is_still():
    import asyncio
    client, polls = _moving_controller([_status(True), _status(True), _status(False)])
    result = asyncio.run(client.arm_pose("MEDICINE"))
    assert result.ok and result.data["moving"] is False and len(polls) == 3


def test_stop_during_motion_fails_the_step():
    import asyncio
    client, _ = _moving_controller([_status(True), _status(False, stop=True)])
    result = asyncio.run(client.arm_pose("MEDICINE"))
    assert not result.ok and "emergency stop" in result.error


def test_motion_that_never_finishes_fails():
    import asyncio
    import itertools
    import httpx
    client, _ = _moving_controller(itertools.repeat(httpx.Response(200, json={"arm": {"moving": True}})))
    result = asyncio.run(client.arm_pose("MEDICINE"))
    assert not result.ok and "did not finish" in result.error
