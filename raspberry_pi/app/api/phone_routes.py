from __future__ import annotations

import asyncio
import base64

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.config import PROJECT_ROOT
from app.models import Action, AssistantResponse, DetectionResult, PhoneCommandResponse
from app.utils.logger import get_logger
from app.vision.detector import InvalidImageError
from app.voice.sarvam import Transcript, VoiceError

logger = get_logger(__name__)
router = APIRouter(tags=["phone"])

PHONE_PAGE = PROJECT_ROOT / "app" / "web" / "phone.html"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


async def _read(upload: UploadFile | None, what: str) -> bytes | None:
    if upload is None:
        return None
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{what} larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    return data or None


@router.get("/phone", response_class=HTMLResponse, include_in_schema=False)
async def phone_page(request: Request) -> HTMLResponse:
    """Bedside phone page: speak or type a command, the camera adds a photo of the table."""
    https_port = request.app.state.settings.https_port
    page = PHONE_PAGE.read_text(encoding="utf-8").replace("__HTTPS_PORT__", str(https_port or ""))
    return HTMLResponse(page)


@router.post("/assistant/phone", response_model=PhoneCommandResponse)
async def phone_command(request: Request, text: str | None = Form(None), audio: UploadFile | None = None,
                        image: UploadFile | None = None) -> PhoneCommandResponse:
    """One bedside command: typed `text` or recorded `audio`, plus an optional `image`
    of the table. The photo is analysed before the command runs, so "where is my phone?"
    uses it."""
    state = request.app.state
    text = (text or "").strip() or None
    audio_bytes = await _read(audio, "Audio")
    image_bytes = await _read(image, "Image")
    if text is None and audio_bytes is None:
        raise HTTPException(400, "Send either text or audio")
    if text is None and state.voice is None:
        raise HTTPException(503, "Voice is disabled: set SARVAM_API_KEY in .env, or type the command")

    async def detect() -> tuple[DetectionResult | None, str | None]:
        if image_bytes is None:
            return None, None
        if state.detector is None:
            return None, "Vision is disabled: set YOLO_MODEL in .env"
        try:
            scene = await state.detector.detect(image_bytes)
        except InvalidImageError as exc:
            return None, str(exc)
        state.locator.update(scene)
        state.monitor.offer_frame(image_bytes, scene)
        return scene, None

    async def listen() -> Transcript | None:
        if text is not None:
            return None
        return await state.voice.transcribe(audio_bytes, audio.content_type or "")

    # Speech-to-text and YOLO run at the same time; both finish before the command runs.
    (scene, vision_error), heard = await _detect_and_listen(detect(), listen())

    command = text if heard is None else heard.text
    if not command:
        raise HTTPException(422, "No speech was recognised. Please try again, a little closer to the phone.")
    result = await state.assistant.handle_text(command)
    state.monitor.log("command", result.ok, f'"{command}" -> {result.response}')

    reply_text, reply_language, reply_audio, voice_error = result.response, None, None, None
    if state.voice is not None:
        settings = state.settings
        spoken = heard.language_code if heard else None
        # Answer in the patient's own language when they didn't speak the reply language.
        if settings.sarvam_reply_in_spoken_language and spoken and spoken != settings.sarvam_tts_language:
            try:
                reply_text = await state.voice.translate(result.response, spoken)
                reply_language = spoken
            except VoiceError as exc:
                voice_error = f"Reply not translated, answering in English ({exc})"
        try:
            audio = await state.voice.synthesize(reply_text, reply_language)
            reply_audio = base64.b64encode(audio).decode("ascii")
        except VoiceError as exc:
            # Retry in English only if Sarvam rejected the language (4xx). After a timeout a
            # retry would just make the patient wait again; the phone's own voice reads the text.
            rejected = exc.status_code is not None and 400 <= exc.status_code < 500
            if reply_language is None or not rejected:
                voice_error = str(exc)  # the reply text is still shown and read out by the browser
            else:  # e.g. a language the voice model can't speak: fall back to English
                reply_text, reply_language = result.response, None
                try:
                    reply_audio = base64.b64encode(await state.voice.synthesize(reply_text)).decode("ascii")
                    voice_error = f"Couldn't speak {spoken}, answered in English ({exc})"
                except VoiceError as exc2:
                    voice_error = str(exc2)

    return PhoneCommandResponse(
        transcript=heard.text if heard else None, language_code=heard.language_code if heard else None,
        assistant=result, vision=scene, vision_error=vision_error, reply_text=reply_text,
        reply_language=reply_language or (state.settings.sarvam_tts_language if state.voice else None),
        reply_audio=reply_audio, voice_error=voice_error,
        dial=_number_to_dial(result, state.settings.contact_phones))


def _number_to_dial(result: AssistantResponse, phones: dict[str, str]) -> str | None:
    """Number for the page to open in the dialler: the requested contact, or the caregiver
    in an emergency. None when the bedside phone already placed the call automatically,
    so nobody is rung twice."""
    if result.action == Action.CALL_CONTACT and result.contact:
        if result.details.get("delivery", {}).get("call_placed"):
            return None
        return phones.get(result.contact)
    if result.action == Action.EMERGENCY:
        if result.details.get("emergency", {}).get("alert", {}).get("call_placed"):
            return None
        return phones.get(result.contact or "caregiver")
    return None


async def _detect_and_listen(detect, listen):
    """Runs both; a speech failure is reported only after YOLO finished, so the scene is still stored."""
    vision, heard = await asyncio.gather(detect, listen, return_exceptions=True)
    if isinstance(vision, BaseException):
        raise vision
    if isinstance(heard, VoiceError):
        raise HTTPException(502, f"Speech recognition failed: {heard}")
    if isinstance(heard, BaseException):
        raise heard
    return vision, heard
