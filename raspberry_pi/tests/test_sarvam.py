import asyncio
import base64
import json

import httpx
import pytest

from app.config import load_settings
from app.voice.sarvam import SarvamVoice, VoiceError
from tests.test_phone import SARVAM_ENV


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def voice_settings(env):
    env.update(SARVAM_ENV)
    return load_settings(env)


def make_voice(settings, handler):
    return SarvamVoice(settings, transport=httpx.MockTransport(handler))


def test_transcribe_strips_codec_parameters_and_sends_key(voice_settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["api-subscription-key"]
        seen["body"] = request.content
        return httpx.Response(200, json={"transcript": " Where is my phone? ", "language_code": "ml-IN"})

    result = run(make_voice(voice_settings, handler).transcribe(b"opus", "audio/webm;codecs=opus"))
    assert result.text == "Where is my phone?" and result.language_code == "ml-IN"
    assert seen["key"] == "sk_test_123456"
    assert b"Content-Type: audio/webm\r\n" in seen["body"]
    assert b"saaras:v4" in seen["body"] and b"translate" in seen["body"]


def test_synthesize_decodes_wav(voice_settings):
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"audios": [base64.b64encode(b"RIFFwav").decode()]})

    assert run(make_voice(voice_settings, handler).synthesize("Hello")) == b"RIFFwav"
    assert sent == {"text": "Hello", "target_language_code": "en-IN", "model": "bulbul:v3", "speaker": "shubh",
                    "output_audio_codec": "wav"}


@pytest.mark.parametrize("response, message", [
    (httpx.Response(403, json={"error": {"message": "Invalid API key"}}), "HTTP 403: Invalid API key"),
    (httpx.Response(200, json={"nothing": True}), "no transcript"),
    (httpx.Response(200, text="not json"), "invalid JSON"),
])
def test_transcribe_errors_become_voice_errors(voice_settings, response, message):
    with pytest.raises(VoiceError, match=message):
        run(make_voice(voice_settings, lambda request: response).transcribe(b"x", "audio/wav"))


def test_unreachable_sarvam_is_a_voice_error(voice_settings):
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(VoiceError, match="unreachable"):
        run(make_voice(voice_settings, handler).synthesize("Hello"))


def test_synthesize_rejects_missing_audio(voice_settings):
    with pytest.raises(VoiceError, match="no audio"):
        run(make_voice(voice_settings, lambda r: httpx.Response(200, json={"audios": []})).synthesize("Hi"))



def test_translate_and_speak_in_another_language(voice_settings):
    sent = []

    def handler(request):
        sent.append((request.url.path, json.loads(request.content)))
        if request.url.path == "/translate":
            return httpx.Response(200, json={"translated_text": " നിങ്ങളുടെ ഫോൺ "})
        return httpx.Response(200, json={"audios": [base64.b64encode(b"RIFF").decode()]})

    voice = make_voice(voice_settings, handler)
    assert run(voice.translate("Your phone", "ml-IN")) == "നിങ്ങളുടെ ഫോൺ"
    run(voice.synthesize("നിങ്ങളുടെ ഫോൺ", "ml-IN"))
    assert sent[0][1]["source_language_code"] == "en-IN" and sent[0][1]["target_language_code"] == "ml-IN"
    assert sent[1][1]["target_language_code"] == "ml-IN"
