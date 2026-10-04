import asyncio
import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.communication.android_gateway import AndroidGatewayChannel
from app.communication.caregiver import CaregiverService, build_caregiver
from app.config import ConfigError, load_settings
from app.main import create_app
from tests.conftest import FakeVoice

GATEWAY_ENV = dict(COMMUNICATION_BACKEND="ANDROID_GATEWAY", ANDROID_GATEWAY_URL="http://10.0.0.5:8080/",
                   ANDROID_GATEWAY_USERNAME="sms", ANDROID_GATEWAY_PASSWORD="secret-pass",
                   ANDROID_GATEWAY_TIMEOUT_S="5", CAREGIVER_PHONE="+91 98765-43210",
                   CONTACT_PHONES="son=+919811111111, Daughter=+919822222222")
SMS_TEXTS = {"message": "From bedside: {message}", "call_request": "Please call the patient"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def gateway_settings(env):
    env.update(GATEWAY_ENV)
    return load_settings(env)


def channel_with(settings, handler):
    return AndroidGatewayChannel(settings, SMS_TEXTS, transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- settings

def test_contact_numbers_are_normalised(gateway_settings):
    assert gateway_settings.contact_phones == {"caregiver": "+919876543210", "son": "+919811111111",
                                               "daughter": "+919822222222"}
    assert gateway_settings.android_gateway_url == "http://10.0.0.5:8080"
    assert "secret-pass" not in repr(gateway_settings)


@pytest.mark.parametrize("missing", ["ANDROID_GATEWAY_URL", "ANDROID_GATEWAY_USERNAME", "ANDROID_GATEWAY_PASSWORD",
                                     "ANDROID_GATEWAY_TIMEOUT_S", "CAREGIVER_PHONE"])
def test_gateway_requires_its_settings(env, missing):
    env.update(GATEWAY_ENV)
    del env[missing]
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


@pytest.mark.parametrize("phones", [{"CAREGIVER_PHONE": "9876543210"}, {"CONTACT_PHONES": "son 919811111111"},
                                    {"CONTACT_PHONES": "son=+91abc"}])
def test_bad_numbers_are_rejected(env, phones):
    env.update(GATEWAY_ENV, **phones)
    with pytest.raises(ConfigError, match="CAREGIVER_PHONE|CONTACT_PHONES"):
        load_settings(env)


def test_numbers_for_unknown_contacts_are_rejected(env, intents):
    env.update(GATEWAY_ENV, CONTACT_PHONES="neighbour=+919833333333")
    with pytest.raises(ConfigError, match="neighbour"):
        build_caregiver(load_settings(env), intents.emergency_contact, SMS_TEXTS, intents.contacts)


# ---------------------------------------------------------------- channel

def test_message_is_sent_as_sms_with_basic_auth(gateway_settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json={"id": "abc", "state": "Pending"})

    run(channel_with(gateway_settings, handler).send_message("son", "I am hungry"))
    assert seen["url"] == "http://10.0.0.5:8080/message"
    assert seen["auth"] == "Basic " + base64.b64encode(b"sms:secret-pass").decode()
    assert seen["body"] == {"textMessage": {"text": "From bedside: I am hungry"}, "phoneNumbers": ["+919811111111"]}


def test_call_sends_a_call_back_request(gateway_settings):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(202)

    run(channel_with(gateway_settings, handler).call("daughter"))
    assert bodies == [{"textMessage": {"text": "Please call the patient"}, "phoneNumbers": ["+919822222222"]}]


@pytest.mark.parametrize("handler, error", [
    (lambda r: httpx.Response(401, text="Unauthorized"), "HTTP 401"),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")), "ConnectError"),
])
def test_gateway_failures_are_reported_not_raised(gateway_settings, handler, error):
    service = CaregiverService(channel_with(gateway_settings, handler), "caregiver")
    result = run(service.send_emergency_alert("help"))
    assert not result.ok and error in result.error


def test_contact_without_number_fails_truthfully(gateway_settings):
    service = CaregiverService(channel_with(gateway_settings, lambda r: httpx.Response(202)), "caregiver")
    result = run(service.send_message("wife", "hello"))
    assert not result.ok and "no phone number for 'wife'" in result.error


# ---------------------------------------------------------------- phone page dialling

@pytest.fixture
def http(env, mock_client, caregiver):
    env.update(CAREGIVER_PHONE="+919876543210", CONTACT_PHONES="son=+919811111111")
    with TestClient(create_app(load_settings(env), controller=mock_client, caregiver=caregiver,
                               detector=None, voice=FakeVoice())) as client:
        yield client


@pytest.mark.parametrize("text, number", [
    ("call my son", "+919811111111"),
    ("help", "+919876543210"),          # emergency: dial the caregiver
    ("call my wife", None),             # no number configured
    ("where is my phone", None),
])
def test_phone_gets_number_to_dial(http, text, number):
    assert http.post("/assistant/phone", data={"text": text}).json()["dial"] == number
