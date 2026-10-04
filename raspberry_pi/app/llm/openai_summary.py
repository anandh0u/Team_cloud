"""Plain-language activity summary for the caregiver, written by an OpenAI model.

Only the report's numbers are sent, never camera pictures. The instructions forbid
medical judgements; the model only rewrites what the numbers already say.
"""
from __future__ import annotations

import json

import httpx

from app.config import Settings
from app.models import ActivityReport
from app.utils.logger import get_logger

logger = get_logger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

INSTRUCTIONS = """You write a short activity summary for the family caregiver of a bedridden patient.
The data comes from a bedside camera that estimates body position and movement. It is a
prototype, not a medical device.

Rules:
- Describe only what the numbers show, using the numbers, and compare with the previous period.
- Never diagnose, and never say or imply the patient is improving, recovering, getting better,
  getting worse, healthy, sick, in pain, sleeping or comfortable. More movement can also mean
  restlessness or discomfort; you can't tell which.
- Never guess causes.
- If camera coverage is low, say the picture is incomplete.
- If activity changed clearly (trend more_active or less_active), suggest mentioning the change to
  the doctor or nurse.
- Round percentages to whole numbers and write durations as minutes or hours.
- Write for a family member: never use the data's field names or codes (say "more active", not
  "more_active"; "moved in 42% of checks", not "active_pct").
- 3 to 5 short sentences in plain English. No markdown, no lists, no greeting."""


class OpenAISummarizer:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._model = settings.llm_model
        self._http = httpx.AsyncClient(timeout=settings.llm_timeout_s, transport=transport,
                                       headers={"Authorization": f"Bearer {settings.openai_api_key}"})

    async def summarize(self, report: ActivityReport) -> str:
        data = report.model_dump(mode="json", exclude={"highlights", "ai_summary", "ai_error", "generated_at"})
        body = {
            "model": self._model,
            "messages": [{"role": "system", "content": INSTRUCTIONS},
                         {"role": "user", "content": json.dumps(data)}],
            "max_completion_tokens": 2000,  # includes the model's hidden reasoning tokens
        }
        try:
            response = await self._http.post(OPENAI_URL, json=body)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenAI unreachable ({type(exc).__name__})") from exc
        if response.status_code != 200:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.text[:200]
            logger.error("openai -> HTTP %d: %s", response.status_code, message)
            raise RuntimeError(f"OpenAI HTTP {response.status_code}: {message}")
        try:
            text = response.json()["choices"][0]["message"]["content"].strip()
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise RuntimeError("OpenAI reply had no text") from exc
        if not text:
            raise RuntimeError("OpenAI reply was empty")
        logger.info("openai summary: %d characters from %s", len(text), self._model)
        return text

    async def aclose(self) -> None:
        await self._http.aclose()
