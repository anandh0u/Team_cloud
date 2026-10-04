import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, PROJECT_ROOT, load_settings
from app.main import create_app
from app.models import Detection, DetectionResult
from app.vision.locator import SceneLocator, load_object_labels
from tests.conftest import FakeDetector, FakeVoice


def run(coro):
    return asyncio.run(coro)


def scene(*detections: Detection) -> DetectionResult:
    return DetectionResult(model="fake.pt", image_width=600, image_height=400, inference_ms=1.0,
                           detections=list(detections))


def det(label: str, x1: float, x2: float = None, confidence: float = 0.9) -> Detection:
    return Detection(label=label, confidence=confidence, box=(x1, 100, x2 if x2 is not None else x1 + 40, 200))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def locator(vision_settings, intents, responses, clock):
    labels = load_object_labels(vision_settings.vision_labels_path, intents.objects)
    return SceneLocator(labels, responses, max_age_s=60, clock=clock)


# ---------------------------------------------------------------- locator

@pytest.mark.parametrize("x1, position", [(10, "left"), (280, "middle"), (500, "right")])
def test_locator_describes_position_by_thirds(locator, responses, x1, position):
    locator.update(scene(det("cell phone", x1)))
    res = run(locator.describe_location("phone"))
    assert res.seen and res.position == position and res.neighbour is None
    assert res.response == responses.get("find_seen", object="phone", position=responses.get(f"position_{position}"))


def test_locator_names_nearest_other_object(locator, responses):
    locator.update(scene(det("cell phone", 10), det("laptop", 80), det("book", 500)))
    res = run(locator.describe_location("phone"))
    assert res.neighbour == "laptop"
    assert res.response == responses.get("find_seen_near", object="phone", position=responses.get("position_left"),
                                         neighbour="laptop")


def test_locator_uses_most_confident_match(locator):
    locator.update(scene(det("bottle", 10, confidence=0.4), det("bottle", 500, confidence=0.95)))
    assert run(locator.describe_location("water")).position == "right"


def test_locator_says_not_seen_instead_of_guessing(locator, responses):
    locator.update(scene(det("laptop", 10)))
    res = run(locator.describe_location("phone"))
    assert not res.seen and res.response == responses.get("find_not_seen", object="phone")


def test_locator_needs_a_recent_photo(locator, responses, clock):
    no_photo = responses.get("find_no_photo", object="phone")
    assert run(locator.describe_location("phone")).response == no_photo
    locator.update(scene(det("cell phone", 10)))
    clock.now += 61
    res = run(locator.describe_location("phone"))
    assert not res.seen and res.response == no_photo


def test_locator_admits_objects_the_model_cannot_recognise(locator, responses):
    locator.update(scene(det("bottle", 10)))
    res = run(locator.describe_location("medicine"))
    assert not res.seen and res.response == responses.get("find_not_trained", object="medicine")


def test_vision_catalog_rejects_unknown_objects(tmp_path):
    path = tmp_path / "vision_labels.json"
    path.write_text('{"object_labels": {"teleporter": ["car"]}}')
    with pytest.raises(ConfigError, match="teleporter"):
        load_object_labels(path, ["phone"])


# ---------------------------------------------------------------- /assistant/phone

@pytest.fixture
def voice():
    return FakeVoice()


@pytest.fixture
def http(vision_settings, mock_client, voice):
    with TestClient(create_app(vision_settings, controller=mock_client, detector=FakeDetector(), voice=voice)) as c:
        yield c


AUDIO = {"audio": ("speech.webm", b"opus bytes", "audio/webm;codecs=opus")}
IMAGE = {"image": ("table.jpg", b"jpeg bytes", "image/jpeg")}


def test_voice_and_photo_answer_where_is_my_phone(http, voice, responses):
    body = http.post("/assistant/phone", files={**AUDIO, **IMAGE}).json()
    assert body["transcript"] == "where is my phone"
    assert body["vision"]["detections"][0]["label"] == "cell phone"
    reply = body["assistant"]
    assert reply["action"] == "FIND_OBJECT" and reply["ok"]
    assert reply["response"] == responses.get("find_seen_near", object="phone",
                                              position=responses.get("position_left"), neighbour="laptop")
    assert base64.b64decode(body["reply_audio"]) == b"RIFFfake-wav"
    assert voice.spoken == [reply["response"]]
    assert voice.transcribed == [(b"opus bytes", "audio/webm;codecs=opus")]


def test_typed_text_skips_speech_recognition(http, voice):
    body = http.post("/assistant/phone", data={"text": "Where is my phone?"}, files=IMAGE).json()
    assert body["transcript"] is None and body["assistant"]["ok"]
    assert voice.transcribed == []


def test_photo_from_vision_endpoint_is_used_by_later_question(http):
    http.post("/vision/detect", files=IMAGE)
    body = http.post("/assistant/phone", data={"text": "where is my phone"}).json()
    assert body["assistant"]["ok"] and body["vision"] is None


def test_emergency_by_voice_still_works_without_photo(http, voice):
    voice.heard = voice.heard.model_copy(update={"text": "help"})
    body = http.post("/assistant/phone", files=AUDIO).json()
    assert body["assistant"]["action"] == "EMERGENCY"


def test_speech_failure_is_502_but_photo_is_kept(http, voice):
    voice.fail_stt = True
    response = http.post("/assistant/phone", files={**AUDIO, **IMAGE})
    assert response.status_code == 502 and "Speech recognition failed" in response.json()["detail"]
    assert http.post("/assistant/phone", data={"text": "where is my phone"}).json()["assistant"]["ok"]


def test_empty_transcript_is_422(http, voice):
    voice.heard = voice.heard.model_copy(update={"text": ""})
    assert http.post("/assistant/phone", files=AUDIO).status_code == 422


def test_tts_failure_still_returns_the_reply_text(http, voice):
    voice.fail_tts = True
    body = http.post("/assistant/phone", data={"text": "where is my phone"}, files=IMAGE).json()
    assert body["assistant"]["response"] and body["reply_audio"] is None
    assert "ReadTimeout" in body["voice_error"]


def test_unreadable_photo_does_not_block_the_command(http):
    files = {"image": ("table.jpg", b"not an image", "image/jpeg")}
    body = http.post("/assistant/phone", data={"text": "where is my phone"}, files=files).json()
    assert body["vision_error"] == "not a readable image"
    assert body["assistant"]["action"] == "FIND_OBJECT"


def test_needs_text_or_audio(http):
    assert http.post("/assistant/phone", files=IMAGE).status_code == 400


def test_audio_without_voice_configured_is_503(settings, mock_client):
    with TestClient(create_app(settings, controller=mock_client)) as http:
        assert http.post("/assistant/phone", files=AUDIO).status_code == 503
        body = http.post("/assistant/phone", data={"text": "where is my phone"}).json()
    assert body["reply_audio"] is None and body["assistant"]["action"] == "FIND_OBJECT"


# ---------------------------------------------------------------- voice + HTTPS settings

SARVAM_ENV = dict(SARVAM_API_KEY="sk_test_123456", SARVAM_STT_MODEL="saaras:v4", SARVAM_STT_MODE="translate",
                  SARVAM_TTS_MODEL="bulbul:v3", SARVAM_TTS_SPEAKER="shubh", SARVAM_TTS_LANGUAGE="en-IN",
                  SARVAM_TIMEOUT_S="20", SARVAM_REPLY_IN_SPOKEN_LANGUAGE="false")


def test_sarvam_settings_load_and_key_is_hidden(env):
    env.update(SARVAM_ENV)
    settings = load_settings(env)
    assert settings.sarvam_stt_mode == "translate"
    assert "sk_test_123456" not in repr(settings)


@pytest.mark.parametrize("missing", [k for k in SARVAM_ENV if k != "SARVAM_API_KEY"])
def test_sarvam_key_requires_the_other_voice_settings(env, missing):
    env.update(SARVAM_ENV)
    del env[missing]
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


def test_sarvam_mode_is_checked(env):
    env.update(SARVAM_ENV, SARVAM_STT_MODE="sing")
    with pytest.raises(ConfigError, match="SARVAM_STT_MODE"):
        load_settings(env)


def test_https_needs_existing_cert_files(env, tmp_path):
    env.update(HTTPS_PORT="8443", TLS_CERT_FILE=str(tmp_path / "missing.pem"), TLS_KEY_FILE=str(tmp_path / "k.pem"))
    with pytest.raises(ConfigError, match="TLS_CERT_FILE"):
        load_settings(env)
    (tmp_path / "missing.pem").write_text("x")
    (tmp_path / "k.pem").write_text("x")
    assert load_settings(env).https_port == 8443


def test_phone_page_links_to_https_port(env, mock_client, tmp_path):
    for name in ("c.pem", "k.pem"):
        (tmp_path / name).write_text("x")
    env.update(HTTPS_PORT="8443", TLS_CERT_FILE=str(tmp_path / "c.pem"), TLS_KEY_FILE=str(tmp_path / "k.pem"))
    with TestClient(create_app(load_settings(env), controller=mock_client)) as http:
        assert 'const HTTPS_PORT = "8443"' in http.get("/phone").text


def test_real_vision_catalog_matches_intents(intents):
    labels = load_object_labels(PROJECT_ROOT / "config" / "vision_labels.json", intents.objects)
    assert labels["phone"] == ["cell phone"] and labels["medicine"] == []


def test_finds_any_object_the_camera_knows(http, voice, responses):
    # Regression: YOLO saw a laptop, but "laptop" wasn't an assistant object, so the
    # reply was "I don't know that object" before the photo was even checked.
    voice.heard = voice.heard.model_copy(update={"text": "Can you find the laptop?"})
    body = http.post("/assistant/phone", files={**AUDIO, **IMAGE}).json()
    assert body["assistant"]["object"] == "laptop" and body["assistant"]["ok"]
    assert body["assistant"]["response"] == responses.get(
        "find_seen_near", object="laptop", position=responses.get("position_middle"), neighbour="cell phone")


def test_tv_remote_is_not_mistaken_for_tv(parser):
    assert parser.parse("where is the tv remote").object == "remote"
    assert parser.parse("where is the tv").object == "tv"



# ---------------------------------------------------------------- replying in the patient's language

@pytest.fixture
def multilingual(env, mock_client, voice):
    env.update(SARVAM_ENV, SARVAM_REPLY_IN_SPOKEN_LANGUAGE="true", SARVAM_TRANSLATE_MODEL="sarvam-translate:v1",
               YOLO_MODEL="data/models/test.pt", YOLO_CONFIDENCE="0.35", SCENE_MAX_AGE_S="60")
    with TestClient(create_app(load_settings(env), controller=mock_client, detector=FakeDetector(),
                               voice=voice)) as client:
        yield client


def test_malayalam_question_gets_a_malayalam_answer(multilingual, voice):
    voice.heard = voice.heard.model_copy(update={"text": "Where is my phone?", "language_code": "ml-IN"})
    body = multilingual.post("/assistant/phone", files={**AUDIO, **IMAGE}).json()
    english = body["assistant"]["response"]
    assert body["reply_text"] == f"[ml-IN] {english}" and body["reply_language"] == "ml-IN"
    assert voice.spoken == [(f"[ml-IN] {english}", "ml-IN")]
    assert body["voice_error"] is None


def test_english_question_is_not_translated(multilingual, voice):
    body = multilingual.post("/assistant/phone", files=AUDIO).json()
    assert body["reply_text"] == body["assistant"]["response"] and body["reply_language"] == "en-IN"


def test_translation_failure_falls_back_to_english(multilingual, voice):
    voice.heard = voice.heard.model_copy(update={"language_code": "hi-IN"})
    voice.fail_translate = True
    body = multilingual.post("/assistant/phone", files=AUDIO).json()
    assert body["reply_text"] == body["assistant"]["response"] and body["reply_audio"]
    assert "answering in English" in body["voice_error"]


def test_unspeakable_language_falls_back_to_english_audio(multilingual, voice):
    voice.heard = voice.heard.model_copy(update={"language_code": "ur-IN"})
    voice.unspeakable = {"ur-IN"}
    body = multilingual.post("/assistant/phone", files=AUDIO).json()
    assert body["reply_text"] == body["assistant"]["response"] and body["reply_audio"]
    assert "answered in English" in body["voice_error"]


def test_translate_model_required_when_replying_in_spoken_language(env):
    env.update(SARVAM_ENV, SARVAM_REPLY_IN_SPOKEN_LANGUAGE="true")
    with pytest.raises(ConfigError, match="SARVAM_TRANSLATE_MODEL"):
        load_settings(env)



def test_slow_network_does_not_retry_speech(multilingual, voice):
    voice.heard = voice.heard.model_copy(update={"language_code": "ml-IN"})
    voice.fail_tts = True
    body = multilingual.post("/assistant/phone", files=AUDIO).json()
    assert body["reply_audio"] is None and body["reply_text"].startswith("[ml-IN]")  # the phone reads it
    assert voice.spoken == []


# ---------------------------------------------------------------- camera-checked fetching

@pytest.fixture
def table(vision_settings, mock_client, voice):
    """App whose camera sees a spoon on the left and a bottle on the right."""
    detector = FakeDetector(scene(det("spoon", 20), det("bottle", 500)))
    with TestClient(create_app(vision_settings, controller=mock_client, detector=detector, voice=voice)) as c:
        yield c


def say(client, text, photo=True):
    return client.post("/assistant/phone", data={"text": text}, files=IMAGE if photo else None).json()["assistant"]


def test_fetch_happens_only_when_the_camera_sees_the_object(table, mock_client, responses):
    reply = say(table, "I want spoon")
    assert reply["ok"] and reply["details"]["camera_verified"] is True
    assert reply["response"] == responses.get("get_done_seen", object="spoon", position=responses.get("position_left"))
    assert [s["action"] for s in reply["details"]["steps"]] == ["GRIPPER_OPEN", "POSE", "GRIPPER_CLOSE", "POSE"]
    assert mock_client.pose == "USER" and mock_client.gripper == "CLOSED"


def test_object_not_on_the_table_means_the_arm_does_not_move(table, mock_client, responses):
    reply = say(table, "bring me my phone")
    assert not reply["ok"] and reply["response"] == responses.get("get_not_seen", object="phone")
    assert "steps" not in reply["details"] and mock_client.pose == "HOME"


def test_no_recent_photo_means_the_arm_does_not_move(vision_settings, mock_client, voice, responses):
    with TestClient(create_app(vision_settings, controller=mock_client, detector=FakeDetector(), voice=voice)) as c:
        reply = say(c, "I want spoon", photo=False)
    assert not reply["ok"] and reply["response"] == responses.get("get_no_photo", object="spoon")
    assert mock_client.pose == "HOME"


def test_objects_the_camera_cannot_recognise_are_fetched_unverified(table, responses):
    reply = say(table, "I need my medicine")
    assert reply["ok"] and reply["details"]["camera_verified"] is False
    assert reply["response"] == responses.get("get_done", object="medicine")


def test_let_go_opens_the_gripper(table, mock_client, responses):
    say(table, "I want spoon")
    reply = say(table, "ok I have it, let go")
    assert reply["action"] == "RELEASE" and reply["ok"] and reply["response"] == responses.get("release_done")
    assert mock_client.gripper == "OPEN"


def test_release_is_refused_after_emergency_stop(table, mock_client):
    say(table, "stop")
    assert not say(table, "let go")["ok"]


def test_uncalibrated_pose_is_never_used(table, mock_client, responses):
    original = mock_client.status

    async def status_with_calibration():
        result = await original()
        return result.model_copy(update={"data": {**result.data, "calibrated": {"SPOON": False}}})

    mock_client.status = status_with_calibration
    reply = say(table, "I want spoon")
    assert not reply["ok"] and reply["response"] == responses.get("get_not_calibrated", object="spoon")
    assert mock_client.pose == "HOME"
