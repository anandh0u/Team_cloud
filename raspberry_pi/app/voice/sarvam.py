"""Sarvam AI speech-to-text and text-to-speech (https://docs.sarvam.ai).

The API key is sent only in the request header and never logged.
"""
from __future__ import annotations

import base64
import time
from typing import Protocol

import httpx
from pydantic import BaseModel

from app.config import Settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

SARVAM_BASE_URL = "https://api.sarvam.ai"
MAX_TTS_CHARS = 2500


class VoiceError(RuntimeError):
    """Sarvam could not be reached (status_code None) or refused the request (its HTTP status)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class Transcript(BaseModel):
    text: str
    language_code: str | None = None


class Voice(Protocol):
    async def transcribe(self, audio: bytes, content_type: str) -> Transcript: ...
    async def synthesize(self, text: str, language_code: str | None = None) -> bytes: ...
    async def translate(self, text: str, target_language_code: str) -> str: ...
    async def aclose(self) -> None: ...


class SarvamVoice:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._s = settings
        self._http = httpx.AsyncClient(base_url=SARVAM_BASE_URL, timeout=settings.sarvam_timeout_s,
                                       headers={"api-subscription-key": settings.sarvam_api_key},
                                       transport=transport)

    async def _post(self, path: str, **kwargs) -> dict:
        started = time.perf_counter()
        try:
            response = await self._http.post(path, **kwargs)
        except httpx.HTTPError as exc:
            logger.error("sarvam POST %s -> unreachable (%s)", path, type(exc).__name__)
            raise VoiceError(f"Sarvam unreachable ({type(exc).__name__})") from exc
        elapsed = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.text[:200]
            logger.error("sarvam POST %s -> %d in %.0f ms: %s", path, response.status_code, elapsed, message)
            raise VoiceError(f"Sarvam HTTP {response.status_code}: {message}", response.status_code)
        logger.info("sarvam POST %s -> 200 in %.0f ms", path, elapsed)
        try:
            return response.json()
        except ValueError as exc:
            raise VoiceError("Sarvam returned invalid JSON") from exc

    async def transcribe(self, audio: bytes, content_type: str) -> Transcript:
        # Sarvam refuses content types with parameters such as "audio/webm;codecs=opus",
        # which is exactly what Chrome's MediaRecorder reports.
        mime = content_type.split(";")[0].strip() or "application/octet-stream"
        body = await self._post("/speech-to-text",
                                files={"file": ("speech", audio, mime)},
                                data={"model": self._s.sarvam_stt_model, "mode": self._s.sarvam_stt_mode})
        text = body.get("transcript")
        if not isinstance(text, str):
            raise VoiceError("Sarvam reply has no transcript")
        return Transcript(text=text.strip(), language_code=body.get("language_code"))

    async def synthesize(self, text: str, language_code: str | None = None) -> bytes:
        """Returns WAV audio for `text`, spoken in `language_code` (default SARVAM_TTS_LANGUAGE)."""
        body = await self._post("/text-to-speech", json={
            "text": text[:MAX_TTS_CHARS],
            "target_language_code": language_code or self._s.sarvam_tts_language,
            "model": self._s.sarvam_tts_model,
            "speaker": self._s.sarvam_tts_speaker,
            "output_audio_codec": "wav",
        })
        try:
            return base64.b64decode(body["audios"][0], validate=True)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise VoiceError("Sarvam reply has no audio") from exc

    async def translate(self, text: str, target_language_code: str) -> str:
        body = await self._post("/translate", json={
            "input": text, "source_language_code": self._s.sarvam_tts_language,
            "target_language_code": target_language_code, "model": self._s.sarvam_translate_model})
        translated = body.get("translated_text")
        if not isinstance(translated, str) or not translated.strip():
            raise VoiceError("Sarvam reply has no translation")
        return translated.strip()

    async def aclose(self) -> None:
        await self._http.aclose()
