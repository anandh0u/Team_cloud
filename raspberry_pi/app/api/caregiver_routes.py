from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.models import CommResult, EmergencyResult

router = APIRouter(tags=["caregiver"])


class MessageRequest(BaseModel):
    contact: str = Field(min_length=1)
    message: str = Field(min_length=1)


class CallRequest(BaseModel):
    contact: str = Field(min_length=1)


class EmergencyRequest(BaseModel):
    source: str = Field(default="api", description="Who triggered it, e.g. phone-button, dashboard")


def _known_contact(request: Request, contact: str) -> str:
    contacts = request.app.state.assistant.parser.catalog.contacts
    key = contact.strip().lower()
    if key not in contacts:
        raise HTTPException(status_code=422, detail=f"unknown contact {contact!r}; known: {sorted(contacts)}")
    return key


@router.post("/caregiver/message", response_model=CommResult)
async def caregiver_message(body: MessageRequest, request: Request, response: Response) -> CommResult:
    result = await request.app.state.caregiver.send_message(_known_contact(request, body.contact), body.message)
    response.status_code = 200 if result.ok else 502
    return result


@router.post("/caregiver/call", response_model=CommResult)
async def caregiver_call(body: CallRequest, request: Request, response: Response) -> CommResult:
    result = await request.app.state.caregiver.call_contact(_known_contact(request, body.contact))
    response.status_code = 200 if result.ok else 502
    return result


@router.post("/emergency", response_model=EmergencyResult)
async def emergency(request: Request, body: EmergencyRequest | None = None) -> EmergencyResult:
    """Always 200 once handled: the body says whether the arm stopped and the caregiver was reached."""
    return await request.app.state.emergency.trigger(source=(body.source if body else "api"))
