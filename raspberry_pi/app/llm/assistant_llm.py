"""AI help for the voice assistant (OpenAI): understanding sentences the fixed rules don't
match, and phrasing replies from what actually happened.

Safety limits:
- Emergency and STOP are always decided by the local rules first; the AI only sees
  sentences that came out UNKNOWN, and can only pick from the real actions, objects and
  contacts (anything else is discarded).
- Replies are written from the facts of the result. The fixed reply is used whenever the
  AI fails or is slow, and emergency/STOP replies never go through the AI.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings
from app.models import Action, AssistantResponse, Intent
from app.utils.logger import get_logger

logger = get_logger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

ACTIONS = {
    "GET_OBJECT": "bring, fetch, pass, hand over, or move the arm to an object",
    "FIND_OBJECT": "where is an object, can you see it",
    "MESSAGE_CONTACT": "send a message to someone (put the message text in 'message')",
    "CALL_CONTACT": "phone someone",
    "HOME": "put the arm back to its resting position",
    "RELEASE": "let go of / drop what the gripper is holding",
    "STATUS": "how the arm or the bedside sensors are doing",
    "STOP": "stop the arm",
    "EMERGENCY": "the person needs urgent help: feels very unwell, fell, chest pain, can't breathe, bleeding",
    "UNKNOWN": "anything else, or unclear",
}
NEEDS_OBJECT = {Action.GET_OBJECT, Action.FIND_OBJECT}
NEEDS_CONTACT = {Action.MESSAGE_CONTACT, Action.CALL_CONTACT}

UNDERSTAND_PROMPT = """You turn what a bedridden patient said into one command for their bedside
robot-arm assistant. Answer with JSON only:
{"action": ..., "object": ..., "contact": ..., "message": ...}

Actions:
%s

object: one of %s, or null. contact: one of %s, or null. Use the closest listed name
("teaspoon" -> "spoon", "my mobile" -> "phone"). Never invent names that aren't listed.
If unsure, use UNKNOWN."""

REPLY_PROMPT = """You are the voice of a bedside assistant for a bedridden patient. Write what to
say back after their request, using ONLY the facts in the JSON.

- One or two short, warm, plain sentences, spoken aloud: no lists, no markdown, no emojis.
- If ok is false, say clearly that it didn't happen and why, and what they can do next.
- Never claim the arm moved, an object was found, or someone was contacted unless the facts
  say so. Never add facts.
- Never offer to do anything ("I can keep looking", "I'll remind you"): the assistant only does
  what the patient asks. You may tell the patient what they can do next.
- No medical advice and no diagnosis.
- "standard_reply" is a correct but plain version: keep its meaning, make it more natural
  and specific to what the patient said."""


class AssistantLLM:
    def __init__(self, settings: Settings, objects: list[str], contacts: list[str],
                 emergency_contacts: list[str], transport: httpx.AsyncBaseTransport | None = None):
        self._model = settings.llm_model
        self._objects = objects
        self._contacts = contacts
        self._emergency_contacts = set(emergency_contacts)
        self._http = httpx.AsyncClient(timeout=settings.llm_assistant_timeout_s, transport=transport,
                                       headers={"Authorization": f"Bearer {settings.openai_api_key}"})

    async def _chat(self, system: str, user: str, json_mode: bool) -> str:
        body: dict[str, Any] = {
            "model": self._model, "max_completion_tokens": 300, "reasoning_effort": "none",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        response = await self._http.post(OPENAI_URL, json=body)
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty reply")
        return text.strip()

    async def understand(self, text: str) -> Intent | None:
        """The closest real command for `text`, or None (unclear, or the AI failed)."""
        system = UNDERSTAND_PROMPT % ("\n".join(f"- {k}: {v}" for k, v in ACTIONS.items()),
                                      ", ".join(self._objects), ", ".join(self._contacts))
        try:
            data = json.loads(await self._chat(system, text, json_mode=True))
            action = Action(data.get("action"))
        except Exception as exc:  # network, HTTP, JSON or an action name that doesn't exist
            logger.warning("llm understand failed: %s", type(exc).__name__)
            return None
        obj = data.get("object") if data.get("object") in self._objects else None
        contact = data.get("contact") if data.get("contact") in self._contacts else None
        message = data.get("message") if isinstance(data.get("message"), str) else None
        if action == Action.UNKNOWN or (action in NEEDS_OBJECT and obj is None) or \
                (action in NEEDS_CONTACT and contact is None):
            return None
        if action == Action.CALL_CONTACT and contact in self._emergency_contacts:
            action = Action.EMERGENCY  # same rule as the local parser
        logger.info("llm understood %r as %s object=%s contact=%s", text, action.value, obj, contact)
        return Intent(action=action, object=obj, contact=contact, message=message, parser="llm",
                      matched="llm", text=text)

    async def reply(self, patient_said: str, result: AssistantResponse) -> str | None:
        """A natural reply built from the result's facts, or None to keep the standard one."""
        details = result.details
        facts = {
            "patient_said": patient_said, "action": result.action.value, "ok": result.ok,
            "standard_reply": result.response, "object": result.object, "contact": result.contact,
            "camera": details.get("camera"), "camera_verified": details.get("camera_verified"),
            "arm_steps": [{"step": s.get("action"), "ok": s.get("ok"), "problem": s.get("message")}
                          for s in details.get("steps", [])] or None,
        }
        try:
            return await self._chat(REPLY_PROMPT, json.dumps(facts, default=str), json_mode=False)
        except Exception as exc:
            logger.warning("llm reply failed (%s): using the standard reply", type(exc).__name__)
            return None

    async def aclose(self) -> None:
        await self._http.aclose()
