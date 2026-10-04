# Raspberry Pi software: the assistant's brain

A FastAPI server on a Raspberry Pi 5. It listens to the patient through a phone on a bedside
stand, sees the bedside table through the same phone's camera, decides what to do, moves the
robotic arm through the ESP32, keeps the caregiver informed, and runs the caregiver's
dashboard and activity reports.

Prototype, **not a medical device**: it never diagnoses anything, and the pulse reading comes
from a hobby sensor.

## How a request flows

```
"I want spoon"  (spoken into the phone page, with live camera frames)
   │
   ├─► Sarvam speech-to-text ──► "I want spoon" (any supported Indian language → English)
   ├─► YOLO on the live frames ──► spoon seen on the left, 3 s ago
   ▼
intent rules (config/intents/en.json) ──► GET_OBJECT spoon          (AI only if no rule matches)
   ▼
router: camera saw it? pose calibrated? ──► ESP32: open → SPOON → close → USER
   ▼                                          (each step waits until the arm is still)
reply from what happened ──► AI phrasing ──► translated back ──► Sarvam speech ──► phone speaker
   ▼
dashboard event log + activity history
```

## Features

| Area | What it does |
|---|---|
| **Voice** | Sarvam speech-to-text with translation, so Malayalam, Hindi, Tamil, etc. work; replies spoken back in the patient's language |
| **Understanding** | Fixed phrase rules decide emergencies and STOP instantly; an OpenAI model handles anything the rules don't match, choosing only real actions, objects and contacts |
| **Vision** | YOLO11n on the phone's live camera (~2 frames/s, ~0.35 s each on the Pi 5 CPU): "where is my phone?", and fetches only happen if the camera saw the object |
| **Arm** | Named poses only; fixed fetch sequence (open → object → grip → hand over); "let go"; STOP at any moment; uncalibrated poses refused |
| **Emergency** | "Help", "I fell", "call Ayisha": the arm stops and the caregiver is called (and texted) at the same time |
| **Calls and texts** | Automatic calls from the bedside phone via MacroDroid; SMS via an Android gateway app; fallback to the phone's dialler |
| **Patient condition** | YOLO pose model: lying / reclined / upright, last movement, out of view; ESP32 pulse + MPU6050; "needs attention" notes |
| **Caregiver dashboard** | Live camera picture with detections, condition, sensors, every request and alert |
| **Activity report** | Day-over-day activity trend, positions, longest still period, 7-day chart, AI-written summary from the numbers only |
| **Access** | Password on every page and API call; HTTPS for the phone's microphone; optional public link via a Cloudflare tunnel |

## Setup

Raspberry Pi OS 64-bit, Python 3.11+.

```bash
cd raspberry_pi
python3 -m venv .venv && source .venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu   # small CPU build
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # then fill it in: every setting is explained there
scripts/make_cert.sh            # HTTPS certificate for the phone (again if the Pi's IP changes)
python run.py                   # http://<Pi IP>:8000 and https://<Pi IP>:8443
```

With `MOCK_HARDWARE=true` (the default) everything runs without the ESP32: the arm, gripper and
sensors are simulated and every simulated value is labelled `MOCK`. The YOLO models (about 6 MB
each) download on first start.

`scripts/start.sh` restarts the server and prints the links; `scripts/start.sh --online` also
opens a Cloudflare quick tunnel with a public `https://….trycloudflare.com` address (it refuses
without `ACCESS_PASSWORD`). The address changes on every start.

### Configuration

Everything is in `.env`; [`.env.example`](.env.example) documents every setting. Required
values **fail closed**: a missing or malformed one stops startup with the variable's name,
instead of falling back to a guess. Each optional feature switches off when its first setting
is empty:

| Feature | Turned on by |
|---|---|
| Real ESP32 | `MOCK_HARDWARE=false` + `ESP32_CONTROLLER_URL` |
| Vision | `YOLO_MODEL` |
| Patient condition, activity history | `YOLO_POSE_MODEL` (+ `DATABASE_PATH` for reports) |
| Voice | `SARVAM_API_KEY` |
| AI understanding, replies, report summaries | `OPENAI_API_KEY` (+ `LLM_ASSISTANT=true`) |
| Real SMS | `COMMUNICATION_BACKEND=ANDROID_GATEWAY` |
| Automatic calls | `CALL_AUTOMATION_URL` |
| Login | `ACCESS_PASSWORD` |

Behaviour that isn't code lives in `config/`:

| File | What |
|---|---|
| `intents/en.json` | every phrase the rules understand, objects and their synonyms, contacts |
| `responses/en.json` | every fixed reply (also the facts the AI rephrases) |
| `poses.json` | the only pose names the Pi may request, and which object uses which pose |
| `vision_labels.json` | which YOLO labels count as which object (`spoon` → `spoon`, `water` → `bottle`) |
| `mock_hardware.json` | simulated sensor values (synthetic, not patient data) |

## Pages

| Page | For |
|---|---|
| `https://<Pi IP>:8443/phone` | the bedside phone: live camera with boxes, 🎤 button, typed commands, spoken replies |
| `/dashboard` | the caregiver: live picture, position, movement, pulse, alerts, every request |
| `/report` | activity trend for the last 24 h / 3 days / 7 days, with an AI summary |
| `/docs` | interactive API documentation |

The phone page needs HTTPS for the microphone. With the home-made certificate the browser warns
once: tap **Advanced → Proceed**. On plain HTTP, typing and the photo button still work.

## What the patient can say

| Say (or similar) | Result |
|---|---|
| "I want spoon", "bring me my water" | camera check, then the arm fetches it and holds it out |
| "Let go", "I have it" | gripper opens |
| "Where is my phone?" | "I can see your phone on the left, next to the laptop." |
| "Go home" | arm back to its resting pose |
| "Stop" | arm freezes; resume from the calibration page or `/arm/resume` |
| "Help", "I fell", "call Ayisha" | **emergency**: arm stops, caregiver called and alerted |
| "Call my son", "tell my daughter I'm hungry" | call / SMS to that contact |
| "How am I doing?" | arm and pulse-sensor status |

The rules are checked in a fixed order: **EMERGENCY > STOP > HOME > RELEASE > STATUS >
MESSAGE > CALL > FIND > GET**. Bare "help" is an emergency; "help me find my phone" is not.
Only sentences that match nothing go to the AI.

## Safety design

- **Emergency and STOP never wait for the network or the AI.** They are matched locally, their
  replies are fixed, and on an emergency the arm stop and the caregiver alert run in parallel,
  so an unreachable ESP32 can't delay the alert.
- **The Pi never sends angles**, only pose names from `poses.json`; the ESP32 checks them against
  its joint limits. Motion commands are sent at most once and never retried automatically.
- **Each fetch step waits until the arm is still** (the Pi polls the ESP32's `/status`), so a
  STOP during a move is never queued behind it, and a stopped step fails the whole fetch.
- **Fetches only happen when the camera saw the object** in the last 10 s, and never to a pose
  the ESP32 reports as uncalibrated. Medicine, which the standard model can't recognise, is
  fetched from its fixed spot.
- **Replies never claim what didn't happen.** The AI only rewrites the facts of the result, and
  the fixed reply is used whenever it is slow or fails.
- **No guessing:** unknown objects and contacts stay unknown; missing or implausible sensor
  readings (e.g. an accelerometer that doesn't feel gravity) are reported as unavailable.
- **Privacy:** camera pictures stay on the Pi. OpenAI receives text and numbers only; the
  history database stores numbers only. Secrets and personal numbers live in the git-ignored `.env`.
- **Not a diagnosis:** the dashboard and reports describe observations ("moved in 42% of checks,
  up from 30%"). The AI is told never to call that improving or worsening.

## Phone setup

**Bedside phone:** Chrome on the `/phone` page, on a stand facing the table. For calls with
no tap, install **MacroDroid**:

1. New macro. Trigger: *Connectivity → HTTP Server Request*, a hard-to-guess path, IP whitelist =
   the Pi's IP, variable whitelist = `number` (create a global string variable `number` first).
   Turn **Send Response** on.
2. Actions: *Phone → Make Call* → `{v=number}`; *Wait 2 s*; *Speakerphone → On*.
3. Battery usage **Unrestricted**. In `.env`: `CALL_AUTOMATION_URL=http://<phone IP>:<port>/<path>`.

**SMS (optional):** on an Android phone with a SIM, install **SMS Gateway for Android**
(sms-gate.app), start its **Local Server**, and put its address, username and password in the
`ANDROID_GATEWAY_*` settings with `COMMUNICATION_BACKEND=ANDROID_GATEWAY`.

## API

| Method | Path | What |
|---|---|---|
| POST | `/assistant/phone` | multipart `text` or `audio`, plus optional `image`: transcript, action, reply text + audio, detections, number to dial |
| POST | `/assistant/text` | `{"text": "Where is my phone?"}` |
| POST | `/vision/detect` | multipart `image`: YOLO detections (also feeds the dashboard and pose analysis) |
| POST | `/find-object` | `{"object": "phone"}` |
| GET | `/health`, `/status`, `/telemetry` | API and ESP32 health, arm state, sensor readings |
| POST | `/arm/pose`, `/arm/home`, `/arm/stop`, `/arm/resume` | direct arm control (pose names only) |
| POST | `/emergency` | stop the arm and alert the caregiver |
| POST | `/caregiver/message`, `/caregiver/call` | contact someone directly |
| GET | `/dashboard/state`, `/dashboard/frame.jpg` | dashboard data and latest camera picture |
| GET | `/report/data?hours=24&ai=true` | activity report (1–168 hours) |

The ESP32's own API is documented in [`../esp32_controller/README.md`](../esp32_controller/README.md).

## Tests

```bash
python -m pytest -q                         # 248 tests, about 5 s, no hardware or internet needed
python scripts/test_esp32.py                # read-only check of the real ESP32
python scripts/test_arm.py --pose USER --yes    # moves the real arm, checks STOP blocks motion
```

The tests use simulated hardware and fake Sarvam, OpenAI, SMS and phone services. They cover
fail-closed config, the ESP32 client (timeouts, retries, waiting for motion), intent rules,
the AI's limits, camera-checked fetching, emergencies, calls and texts, the dashboard, reports
and the login.

## Code layout

```
app/
  main.py              builds the app: services, routes, startup and shutdown
  config.py            reads and validates .env
  api/                 HTTP routes (phone, assistant, vision, dashboard, report, hardware, caregiver) + login
  assistant/           intent rules, emergency handling, router (intent → actions → reply), fixed replies
  vision/              YOLO detector, object locator ("on the left, next to the laptop")
  voice/               Sarvam speech-to-text, translation, text-to-speech
  llm/                 OpenAI: understanding + replies, report summaries
  hardware/            ESP32 client, simulator, arm service, telemetry
  patient/             pose model, condition tracker, monitor, history (SQLite), reports
  communication/       caregiver service, SMS gateway, MacroDroid dialler, mock channel
  web/                 phone, dashboard and report pages
config/                phrases, replies, poses, vision labels, simulator values
scripts/               start.sh, make_cert.sh, hardware test scripts
tests/                 pytest suite
```
