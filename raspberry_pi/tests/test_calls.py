import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.communication.caregiver import CaregiverService
from app.communication.mock import MockChannel
from app.communication.phone_dialer import PhoneAutomationDialer
from app.config import ConfigError, load_settings
from app.main import create_app
from tests.conftest import FakeVoice

PHONES = {"caregiver": "+919876543210", "son": "+919811111111"}
CALL_ENV = dict(CALL_AUTOMATION_URL="http://10.0.0.7:8090/bedside-call-abc123", CALL_AUTOMATION_TIMEOUT_S="5")


def run(coro):
    return asyncio.run(coro)


class FakeDialer:
    name = "PHONE_AUTOMATION"

    def __init__(self, fail=False):
        self.fail = fail
        self.called: list[str] = []

    async def call(self, number: str) -> None:
        if self.fail:
            raise ConnectionError("phone not reachable")
        self.called.append(number)


def service(dialer=None, channel=None):
    svc = CaregiverService(channel or MockChannel(), "caregiver", dialer, PHONES)
    svc.events = []
    svc.on_result = svc.events.append
    return svc


# ---------------------------------------------------------------- dialler client

def test_dialler_sends_number_url_encoded(env):
    env.update(CALL_ENV)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text="ok")

    run(PhoneAutomationDialer(load_settings(env), transport=httpx.MockTransport(handler)).call("+919811111111"))
    assert seen == ["http://10.0.0.7:8090/bedside-call-abc123?number=%2B919811111111"]


def test_dialler_reports_http_errors(env):
    env.update(CALL_ENV)
    dialer = PhoneAutomationDialer(load_settings(env), transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(ConnectionError, match="404"):
        run(dialer.call("+919811111111"))


def test_call_settings(env):
    env.update(CALL_ENV)
    del env["CALL_AUTOMATION_TIMEOUT_S"]
    with pytest.raises(ConfigError, match="CALL_AUTOMATION_TIMEOUT_S"):
        load_settings(env)
    env.update(CALL_ENV, CALL_AUTOMATION_URL="10.0.0.7:8090/x")
    with pytest.raises(ConfigError, match="CALL_AUTOMATION_URL"):
        load_settings(env)


# ---------------------------------------------------------------- service

def test_call_goes_through_the_phone_not_sms():
    dialer, channel = FakeDialer(), MockChannel()
    result = run(service(dialer, channel).call_contact("son"))
    assert result.ok and result.call_placed and result.backend == "PHONE_AUTOMATION"
    assert dialer.called == ["+919811111111"] and channel.sent == []


def test_call_without_number_fails_truthfully():
    result = run(service(FakeDialer()).call_contact("wife"))
    assert not result.ok and not result.call_placed and "no phone number for 'wife'" in result.error


def test_without_dialler_calls_use_the_channel():
    result = run(service(channel=(channel := MockChannel())).call_contact("son"))
    assert result.ok and not result.call_placed and channel.sent[0]["kind"] == "call"


def test_emergency_texts_and_calls_the_caregiver():
    dialer, channel = FakeDialer(), MockChannel()
    svc = service(dialer, channel)
    result = run(svc.send_emergency_alert("Help was requested"))
    assert result.ok and result.call_placed
    assert dialer.called == ["+919876543210"] and channel.sent[0]["kind"] == "message"
    assert sorted(e.kind for e in svc.events) == ["call", "emergency_alert"]  # both on the dashboard


@pytest.mark.parametrize("sms_fails, call_fails, ok", [(True, False, True), (False, True, True), (True, True, False)])
def test_emergency_alert_counts_if_either_gets_through(sms_fails, call_fails, ok):
    channel = MockChannel()
    channel.fail = sms_fails
    result = run(service(FakeDialer(fail=call_fails), channel).send_emergency_alert("help"))
    assert result.ok is ok and result.call_placed is not call_fails
    assert (result.error is None) is (not sms_fails and not call_fails)


# ---------------------------------------------------------------- phone page

@pytest.fixture
def make_http(env, mock_client):
    clients = []

    def make(dialer):
        env.update(CAREGIVER_PHONE=PHONES["caregiver"], CONTACT_PHONES=f"son={PHONES['son']}")
        settings = load_settings(env)
        app = create_app(settings, controller=mock_client, voice=FakeVoice(),
                         caregiver=CaregiverService(MockChannel(), "caregiver", dialer, settings.contact_phones))
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.mark.parametrize("text", ["call my son", "help"])
def test_page_does_not_dial_again_when_the_phone_already_called(make_http, text):
    body = make_http(FakeDialer()).post("/assistant/phone", data={"text": text}).json()
    assert body["assistant"]["ok"] and body["dial"] is None


@pytest.mark.parametrize("text, number", [("call my son", PHONES["son"]), ("help", PHONES["caregiver"])])
def test_page_falls_back_to_dialler_when_automatic_call_fails(make_http, text, number):
    body = make_http(FakeDialer(fail=True)).post("/assistant/phone", data={"text": text}).json()
    assert body["dial"] == number
