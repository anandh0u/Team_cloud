import asyncio
import json

import httpx
import pytest

from app.assistant.emergency import EmergencyHandler
from app.assistant.router import AssistantRouter
from app.config import ConfigError, load_settings
from app.hardware.arm import ArmService
from app.hardware.telemetry import TelemetryService
from app.llm.assistant_llm import AssistantLLM
from app.models import Action
from tests.test_report import LLM_ENV


def run(coro):
    return asyncio.run(coro)


class FakeOpenAI:
    """Answers the 'understand' call with `intent` (JSON) and the reply call with `reply`."""

    def __init__(self, intent=None, reply="Sure, here it is.", fail=False):
        self.intent, self.reply, self.fail = intent, reply, fail
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if self.fail:
            return httpx.Response(500, json={"error": {"message": "down"}})
        content = json.dumps(self.intent) if "response_format" in body else self.reply
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture
def llm_settings(env):
    env.update(LLM_ENV, LLM_ASSISTANT="true", LLM_ASSISTANT_TIMEOUT_S="6")
    return load_settings(env)


@pytest.fixture
def make_router(llm_settings, parser, intents, mock_client, poses, responses, caregiver):
    def make(openai: FakeOpenAI):
        llm = AssistantLLM(llm_settings, list(intents.objects), list(intents.contacts),
                           intents.emergency_call_contacts, transport=httpx.MockTransport(openai))
        arm = ArmService(mock_client, poses, responses)
        return AssistantRouter(parser, arm, TelemetryService(mock_client), caregiver,
                               EmergencyHandler(arm, caregiver, responses), responses, llm=llm)
    return make


def test_unmatched_sentence_is_understood_by_ai(make_router, mock_client):
    openai = FakeOpenAI(intent={"action": "GET_OBJECT", "object": "spoon"}, reply="Getting your spoon now.")
    res = run(make_router(openai).handle_text("Can you move to the spoon?"))
    assert res.action == Action.GET_OBJECT and res.object == "spoon" and res.ok
    assert res.response == "Getting your spoon now." and res.details["standard_response"]
    assert mock_client.pose == "USER"


def test_ai_cannot_invent_objects(make_router, mock_client):
    openai = FakeOpenAI(intent={"action": "GET_OBJECT", "object": "chainsaw"}, reply="Sorry, I can't.")
    res = run(make_router(openai).handle_text("hand me the chainsaw thing"))
    assert res.action == Action.UNKNOWN and mock_client.pose == "HOME"


def test_rule_matches_never_ask_the_ai_to_understand(make_router):
    openai = FakeOpenAI(reply="Here you go.")
    run(make_router(openai).handle_text("bring me my spoon"))
    assert all("response_format" not in r for r in openai.requests)  # only the reply call


@pytest.mark.parametrize("text, action", [("help", Action.EMERGENCY), ("stop", Action.STOP)])
def test_emergency_and_stop_never_go_through_the_ai(make_router, responses, text, action):
    openai = FakeOpenAI(reply="Something reworded")
    res = run(make_router(openai).handle_text(text))
    assert res.action == action and openai.requests == [] and res.response != "Something reworded"


def test_ai_reply_gets_only_facts_and_says_when_nothing_happened(make_router, mock_client):
    mock_client.emergency_stop = True  # the arm will refuse to move
    openai = FakeOpenAI(reply="I couldn't move the arm because it is stopped for safety.")
    res = run(make_router(openai).handle_text("bring me my water"))
    facts = json.loads(openai.requests[-1]["messages"][1]["content"])
    assert facts["ok"] is False and facts["patient_said"] == "bring me my water"
    assert facts["arm_steps"][0]["ok"] is False
    assert not res.ok and res.response.startswith("I couldn't")


def test_ai_failure_falls_back_to_the_rules_and_fixed_reply(make_router, responses):
    res = run(make_router(FakeOpenAI(fail=True)).handle_text("bring me my water"))
    assert res.ok and res.response == responses.get("get_done", object="water")
    assert "standard_response" not in res.details


def test_ai_calling_the_caregiver_is_an_emergency(make_router):
    openai = FakeOpenAI(intent={"action": "CALL_CONTACT", "contact": "caregiver"})
    res = run(make_router(openai).handle_text("could you ring the nurse for me"))
    assert res.action == Action.EMERGENCY


def test_llm_assistant_needs_its_timeout(env):
    env.update(LLM_ENV, LLM_ASSISTANT="true")
    with pytest.raises(ConfigError, match="LLM_ASSISTANT_TIMEOUT_S"):
        load_settings(env)
