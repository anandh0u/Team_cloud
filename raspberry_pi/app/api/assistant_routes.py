from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.models import Action, AssistantResponse, Intent

router = APIRouter(tags=["assistant"])


class TextRequest(BaseModel):
    text: str = Field(min_length=1)


class FindObjectRequest(BaseModel):
    object: str = Field(min_length=1)


@router.post("/assistant/text", response_model=AssistantResponse)
async def assistant_text(body: TextRequest, request: Request) -> AssistantResponse:
    return await request.app.state.assistant.handle_text(body.text)


@router.post("/find-object", response_model=AssistantResponse)
async def find_object(body: FindObjectRequest, request: Request) -> AssistantResponse:
    assistant = request.app.state.assistant
    obj = assistant.parser.resolve_object(body.object)
    intent = Intent(action=Action.FIND_OBJECT, object=obj, matched="api", text=body.object)
    return await assistant.execute(intent)
